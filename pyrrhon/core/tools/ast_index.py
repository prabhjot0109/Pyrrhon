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
import os
import sqlite3
import threading
import time
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


# Within one model turn the index tools (repo_map, find_symbol,
# list_dependencies) fire back-to-back, and each ensure_fresh() re-walks the
# whole tree stat'ing every .py file. Collapsing calls inside this window into
# one walk is the biggest per-turn latency lever on large repos; a couple of
# seconds of staleness is invisible in a voice conversation (turns are slower).
INDEX_FRESH_TTL_SEC = 2.0


class SymbolIndex:
    """Lazy per-repo symbol index. __init__ does no I/O — first use builds it."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.db_path = self.root / ".pyrrhon" / "cache.db"
        self._lock = asyncio.Lock()  # serializes concurrent ensure_fresh calls
        self._last_fresh_at: float | None = None  # monotonic; None = never walked
        self._db: sqlite3.Connection | None = None
        # Every DB touch runs in a to_thread worker; serialize the one shared
        # connection across those threads with a plain Lock.
        self._db_lock = threading.Lock()

    async def ensure_fresh(self, force: bool = False) -> None:
        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._last_fresh_at is not None
                and now - self._last_fresh_at < INDEX_FRESH_TTL_SEC
            ):
                return  # walked moments ago (same turn) — skip the re-scan
            await asyncio.to_thread(self._sync_ensure_fresh)
            self._last_fresh_at = time.monotonic()

    async def find_symbol(self, name: str) -> list[tuple[str, int, str]]:
        return await asyncio.to_thread(self._sync_find_symbol, name)

    async def find_references(self, name: str) -> list[tuple[str, int]]:
        return await asyncio.to_thread(self._sync_find_references, name)

    async def list_imports(self, rel_file: str) -> list[str]:
        return await asyncio.to_thread(self._sync_list_imports, rel_file)

    async def find_importers(self, rel_file: str) -> list[str]:
        return await asyncio.to_thread(self._sync_find_importers, rel_file)

    async def build_repo_map(self, max_chars: int = 6000) -> str:
        return await asyncio.to_thread(self._sync_build_repo_map, max_chars)

    # -- everything below runs in a worker thread ---------------------------

    def _db_conn(self) -> sqlite3.Connection:
        """The one persistent connection, shared across to_thread workers and
        serialized by _db_lock. Opened lazily; the schema runs once. Replaces the
        old connect()+executescript() that ran on every single query."""
        if self._db is None:
            self.db_path.parent.mkdir(exist_ok=True)
            # check_same_thread=False: to_thread runs us on pool threads. Safe
            # because every use below is wrapped in `with self._db_lock`.
            self._db = sqlite3.connect(self.db_path, check_same_thread=False)
            self._db.executescript(_SCHEMA)
        return self._db

    def _iter_files_with_mtime(self):
        """Yield (path, mtime) for every repo .py file, pruning SKIP_DIRS.

        Uses os.scandir so each mtime comes from the DirEntry stat cached by
        the single directory read, not a fresh per-file Path.stat() syscall.
        On Windows that is ~160x faster — per-file stat syscalls dominate the
        walk — with identical freshness: every file is still stat'd each call.
        """
        stack = [str(self.root)]
        while stack:
            try:
                scan = os.scandir(stack.pop())
            except OSError:
                continue  # unreadable dir: skip, don't abort the whole index
            with scan:
                for entry in scan:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIP_DIRS:
                            stack.append(entry.path)
                    elif entry.name.endswith(".py") and entry.is_file(
                        follow_symlinks=False
                    ):
                        yield Path(entry.path), entry.stat().st_mtime

    def _sync_ensure_fresh(self) -> None:
        with self._db_lock:
            conn = self._db_conn()
            known: dict[str, float] = dict(
                conn.execute("SELECT path, mtime FROM files")
            )
            # Per-call construction: Parser objects are not thread-safe. Built via
            # py-tree-sitter (bytes API); the pack's get_parser returns a
            # rust-native parser with a different (str-based) interface.
            parser = Parser(_PY_LANGUAGE)
            seen: set[str] = set()
            for path, mtime in self._iter_files_with_mtime():
                rel = path.relative_to(self.root).as_posix()
                seen.add(rel)
                if known.get(rel) == mtime:
                    continue  # unchanged — the whole point of the mtime column
                self._reparse(conn, parser, path, rel, mtime)
            for rel in set(known) - seen:  # files deleted since last index
                self._forget(conn, rel)
            conn.commit()

    def _reparse(self, conn: sqlite3.Connection, parser, path: Path, rel: str, mtime: float) -> None:
        tree = parser.parse(path.read_bytes())
        conn.execute("DELETE FROM symbols WHERE file = ?", (rel,))
        conn.execute("DELETE FROM refs WHERE file = ?", (rel,))
        conn.execute("DELETE FROM imports WHERE file = ?", (rel,))
        symbols = [
            (node.text.decode("utf-8"), capture_name.removeprefix("def."), rel,
             node.start_point.row + 1)
            for capture_name, nodes in QueryCursor(_DEF_QUERY).captures(tree.root_node).items()
            for node in nodes
        ]
        if symbols:  # one executemany beats N executes on a cold build
            conn.executemany(
                "INSERT INTO symbols (name, kind, file, line) VALUES (?, ?, ?, ?)",
                symbols,
            )
        refs = [
            (node.text.decode("utf-8"), rel, node.start_point.row + 1)
            for nodes in QueryCursor(_REF_QUERY).captures(tree.root_node).values()
            for node in nodes
        ]
        if refs:
            conn.executemany("INSERT INTO refs (name, file, line) VALUES (?, ?, ?)", refs)
        package = _package_of(rel)
        imports = [
            (rel, module)
            for nodes in QueryCursor(_IMPORT_QUERY).captures(tree.root_node).values()
            for node in nodes
            for module in _modules_from_import(node.text.decode("utf-8"), package)
        ]
        if imports:
            conn.executemany("INSERT INTO imports (file, module) VALUES (?, ?)", imports)
        conn.execute("INSERT OR REPLACE INTO files (path, mtime) VALUES (?, ?)", (rel, mtime))

    def _forget(self, conn: sqlite3.Connection, rel: str) -> None:
        conn.execute("DELETE FROM symbols WHERE file = ?", (rel,))
        conn.execute("DELETE FROM refs WHERE file = ?", (rel,))
        conn.execute("DELETE FROM imports WHERE file = ?", (rel,))
        conn.execute("DELETE FROM files WHERE path = ?", (rel,))

    def _sync_find_symbol(self, name: str) -> list[tuple[str, int, str]]:
        with self._db_lock:
            rows = self._db_conn().execute(
                "SELECT file, line, kind FROM symbols WHERE name = ? ORDER BY file, line",
                (name,),
            ).fetchall()
        return [(file, line, kind) for file, line, kind in rows]

    def _sync_find_references(self, name: str) -> list[tuple[str, int]]:
        with self._db_lock:
            rows = self._db_conn().execute(
                "SELECT file, line FROM refs WHERE name = ? ORDER BY file, line",
                (name,),
            ).fetchall()
        return [(file, line) for file, line in rows]

    def _sync_list_imports(self, rel_file: str) -> list[str]:
        with self._db_lock:
            rows = self._db_conn().execute(
                "SELECT DISTINCT module FROM imports WHERE file = ? ORDER BY module",
                (rel_file,),
            ).fetchall()
        return [module for (module,) in rows]

    def _sync_find_importers(self, rel_file: str) -> list[str]:
        module = _module_name(rel_file)
        with self._db_lock:
            rows = self._db_conn().execute(
                "SELECT DISTINCT file FROM imports WHERE module = ? "
                "AND file != ? ORDER BY file",
                (module, rel_file),
            ).fetchall()
        return [file for (file,) in rows]

    def _sync_build_repo_map(self, max_chars: int) -> str:
        """Aider-style repo map: files ordered by how much the rest of the
        repo references their symbols; top symbols listed per file. Pure
        counting over the refs table — no graph traversal, so import cycles
        cannot recurse."""
        with self._db_lock:
            rows = self._db_conn().execute(
                """
                SELECT s.file, s.name, s.kind, s.line,
                       (SELECT COUNT(*) FROM refs r
                         WHERE r.name = s.name AND r.file != s.file) AS uses
                FROM symbols s
                ORDER BY s.file, uses DESC, s.line
                """
            ).fetchall()
        by_file: dict[str, list[tuple[str, str, int, int]]] = {}
        for file, name, kind, line, uses in rows:
            by_file.setdefault(file, []).append((name, kind, line, uses))
        ranked = sorted(
            by_file.items(),
            key=lambda item: sum(u for *_ignored, u in item[1]),
            reverse=True,
        )
        lines: list[str] = []
        used = 0
        for file, symbols in ranked:
            block = [f"{file}:"]
            for name, kind, line, uses in symbols[:8]:
                suffix = f" ({uses} refs)" if uses else ""
                block.append(f"  {kind} {name}:{line}{suffix}")
            chunk = "\n".join(block)
            if used + len(chunk) + 1 > max_chars:
                lines.append("…[truncated — ask about specific files]")
                break
            lines.append(chunk)
            used += len(chunk) + 1
        return "\n".join(lines) or "No symbols indexed yet."


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


class RepoMapTool(Tool):
    name = "repo_map"
    description = (
        "Ranked overview of the whole repo: the most-referenced classes and "
        "functions per file, hottest files first. Call this FIRST on a "
        "codebase you haven't explored — it tells you where to look."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, index: SymbolIndex):
        self.index = index

    async def run(self) -> str:
        await self.index.ensure_fresh()
        return await self.index.build_repo_map()
