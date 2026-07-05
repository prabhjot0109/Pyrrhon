"""Tree-sitter symbol index: definitions + call references, cached in SQLite.

This is the flagship real-time-discipline module: tree-sitter parsing and
SQLite writes are CPU-bound, so *all* of it runs inside asyncio.to_thread().
If cold-indexing a huge repo is ever too slow, the M5+ optimization is to swap
the to_thread call for a ProcessPoolExecutor — the async interface stays put.

Python-only grammar in M4. tree-sitter-language-pack bundles 305+ grammars
behind get_language(name), so additional languages later are a config entry
(a name → query-source mapping), not a rewrite.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from tree_sitter import Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language

from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.repo import SKIP_DIRS

_PY_LANGUAGE = get_language("python")

_DEF_QUERY = Query(
    _PY_LANGUAGE,
    """
    (function_definition name: (identifier) @def.function)
    (class_definition name: (identifier) @def.class)
    """,
)

# "References" in M4 = call sites: plain calls and method calls.
_REF_QUERY = Query(
    _PY_LANGUAGE,
    """
    (call function: (identifier) @ref)
    (call function: (attribute attribute: (identifier) @ref))
    """,
)

# Whole import statements — their text is parsed in Python, which is robust
# across grammar details.
_IMPORT_QUERY = Query(
    _PY_LANGUAGE,
    """
    (import_statement) @import
    (import_from_statement) @import
    """,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL);
CREATE TABLE IF NOT EXISTS symbols (name TEXT, kind TEXT, file TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS refs (name TEXT, file TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS imports (file TEXT, module TEXT);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols (name);
CREATE INDEX IF NOT EXISTS idx_refs_name ON refs (name);
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports (module);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports (file);
"""


def _module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_of(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts:
        parts.pop()  # __init__ or the module file — either way, drop it
    return ".".join(parts)


def _modules_from_import(stmt_text: str, package: str) -> list[str]:
    """Module names referenced by one import statement.

    from-imports also record `module.name` for each imported name: the name
    may be a submodule (`from pkg import api`) or an attribute — a false
    attribute edge is harmless, a missed submodule edge is not.
    """
    text = " ".join(stmt_text.split())
    if text.startswith("from "):
        module_part, _, names_part = text[len("from "):].partition(" import ")
        module = _resolve_relative(module_part.strip(), package)
        if not module:
            return []
        if names_part.strip() == "*":
            return [module]
        modules = [module]
        for name in names_part.replace("(", "").replace(")", "").split(","):
            name = name.strip().split(" as ")[0].strip()
            if name:
                modules.append(f"{module}.{name}")
        return modules
    modules = []
    for part in text[len("import "):].split(","):
        module = part.strip().split(" as ")[0].strip()
        if module:
            modules.append(module)
    return modules


def _resolve_relative(module: str, package: str) -> str:
    if not module.startswith("."):
        return module
    dots = len(module) - len(module.lstrip("."))
    remainder = module.lstrip(".")
    parts = package.split(".") if package else []
    if dots - 1:
        parts = parts[: -(dots - 1)] if len(parts) >= dots - 1 else []
    base = ".".join(parts)
    if remainder and base:
        return f"{base}.{remainder}"
    return remainder or base


class SymbolIndex:
    """Lazy per-repo symbol index. __init__ does no I/O — first use builds it."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.db_path = self.root / ".pyrrhon" / "cache.db"
        self._lock = asyncio.Lock()  # serializes concurrent ensure_fresh calls

    async def ensure_fresh(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_ensure_fresh)

    async def find_symbol(self, name: str) -> list[tuple[str, int, str]]:
        return await asyncio.to_thread(self._sync_find_symbol, name)

    async def find_references(self, name: str) -> list[tuple[str, int]]:
        return await asyncio.to_thread(self._sync_find_references, name)

    async def list_imports(self, rel_file: str) -> list[str]:
        return await asyncio.to_thread(self._sync_list_imports, rel_file)

    async def find_importers(self, rel_file: str) -> list[str]:
        return await asyncio.to_thread(self._sync_find_importers, rel_file)

    # -- everything below runs in a worker thread ---------------------------

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_SCHEMA)
        return conn

    def _python_files(self):
        for path in sorted(self.root.rglob("*.py")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.is_file():
                yield path

    def _sync_ensure_fresh(self) -> None:
        conn = self._connect()
        try:
            known: dict[str, float] = dict(
                conn.execute("SELECT path, mtime FROM files")
            )
            # Per-call construction: Parser objects are not thread-safe. Built via
            # py-tree-sitter (bytes API); the pack's get_parser returns a
            # rust-native parser with a different (str-based) interface.
            parser = Parser(_PY_LANGUAGE)
            seen: set[str] = set()
            for path in self._python_files():
                rel = path.relative_to(self.root).as_posix()
                seen.add(rel)
                mtime = path.stat().st_mtime
                if known.get(rel) == mtime:
                    continue  # unchanged — the whole point of the mtime column
                self._reparse(conn, parser, path, rel, mtime)
            for rel in set(known) - seen:  # files deleted since last index
                self._forget(conn, rel)
            conn.commit()
        finally:
            conn.close()

    def _reparse(self, conn: sqlite3.Connection, parser, path: Path, rel: str, mtime: float) -> None:
        tree = parser.parse(path.read_bytes())
        conn.execute("DELETE FROM symbols WHERE file = ?", (rel,))
        conn.execute("DELETE FROM refs WHERE file = ?", (rel,))
        for capture_name, nodes in QueryCursor(_DEF_QUERY).captures(tree.root_node).items():
            kind = capture_name.removeprefix("def.")  # "function" | "class"
            for node in nodes:
                conn.execute(
                    "INSERT INTO symbols (name, kind, file, line) VALUES (?, ?, ?, ?)",
                    (node.text.decode("utf-8"), kind, rel, node.start_point.row + 1),
                )
        for nodes in QueryCursor(_REF_QUERY).captures(tree.root_node).values():
            for node in nodes:
                conn.execute(
                    "INSERT INTO refs (name, file, line) VALUES (?, ?, ?)",
                    (node.text.decode("utf-8"), rel, node.start_point.row + 1),
                )
        conn.execute("DELETE FROM imports WHERE file = ?", (rel,))
        package = _package_of(rel)
        for nodes in QueryCursor(_IMPORT_QUERY).captures(tree.root_node).values():
            for node in nodes:
                stmt = node.text.decode("utf-8")
                for module in _modules_from_import(stmt, package):
                    conn.execute(
                        "INSERT INTO imports (file, module) VALUES (?, ?)",
                        (rel, module),
                    )
        conn.execute("INSERT OR REPLACE INTO files (path, mtime) VALUES (?, ?)", (rel, mtime))

    def _forget(self, conn: sqlite3.Connection, rel: str) -> None:
        conn.execute("DELETE FROM symbols WHERE file = ?", (rel,))
        conn.execute("DELETE FROM refs WHERE file = ?", (rel,))
        conn.execute("DELETE FROM imports WHERE file = ?", (rel,))
        conn.execute("DELETE FROM files WHERE path = ?", (rel,))

    def _sync_find_symbol(self, name: str) -> list[tuple[str, int, str]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT file, line, kind FROM symbols WHERE name = ? ORDER BY file, line",
                (name,),
            ).fetchall()
        finally:
            conn.close()
        return [(file, line, kind) for file, line, kind in rows]

    def _sync_find_references(self, name: str) -> list[tuple[str, int]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT file, line FROM refs WHERE name = ? ORDER BY file, line",
                (name,),
            ).fetchall()
        finally:
            conn.close()
        return [(file, line) for file, line in rows]

    def _sync_list_imports(self, rel_file: str) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT module FROM imports WHERE file = ? ORDER BY module",
                (rel_file,),
            ).fetchall()
        finally:
            conn.close()
        return [module for (module,) in rows]

    def _sync_find_importers(self, rel_file: str) -> list[str]:
        module = _module_name(rel_file)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT file FROM imports WHERE module = ? "
                "AND file != ? ORDER BY file",
                (module, rel_file),
            ).fetchall()
        finally:
            conn.close()
        return [file for (file,) in rows]


class FindSymbolTool(Tool):
    name = "find_symbol"
    description = (
        "Find where a function or class is defined. Returns 'path:line: kind name' "
        "for each definition — cite these locations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact symbol name, e.g. 'greet'"},
        },
        "required": ["name"],
    }

    def __init__(self, index: SymbolIndex):
        self.index = index

    async def run(self, name: str) -> str:
        await self.index.ensure_fresh()
        rows = await self.index.find_symbol(name)
        if not rows:
            return "No matches."
        return "\n".join(f"{file}:{line}: {kind} {name}" for file, line, kind in rows)


class FindReferencesTool(Tool):
    name = "find_references"
    description = (
        "Find call sites of a function or method by name. Returns 'path:line' per "
        "reference — answers 'what calls this?' / 'what breaks if I change this?'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact symbol name, e.g. 'greet'"},
        },
        "required": ["name"],
    }

    def __init__(self, index: SymbolIndex):
        self.index = index

    async def run(self, name: str) -> str:
        await self.index.ensure_fresh()
        rows = await self.index.find_references(name)
        if not rows:
            return "No matches."
        return "\n".join(f"{file}:{line}" for file, line in rows)


class DependenciesTool(Tool):
    name = "list_dependencies"
    description = (
        "Show a Python file's import edges both ways: modules it imports, and "
        "repo files that import it. Answers 'what depends on this?' / "
        "'what does this rely on?' before you trace call sites."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative .py path"},
        },
        "required": ["path"],
    }

    def __init__(self, index: SymbolIndex):
        self.index = index

    async def run(self, path: str) -> str:
        await self.index.ensure_fresh()
        imports = await self.index.list_imports(path)
        importers = await self.index.find_importers(path)
        if not imports and not importers:
            return f"No import edges recorded for {path} (is it a Python file in the repo?)."
        lines = ["imports:"]
        lines += [f"  {m}" for m in imports] or ["  (none)"]
        lines.append("imported by:")
        lines += [f"  {f}" for f in importers] or ["  (none)"]
        return "\n".join(lines)
