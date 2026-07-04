# Pyrrhon M4 — Deep Reasoning Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Interface drift warning:** written before M0–M3 landed. Before executing, revalidate every Consumes signature against the actual codebase and update this plan if drifted.

**Goal:** Give the agent its deep-reasoning toolkit: a tree-sitter symbol index (SQLite-cached, mtime-invalidated) behind `find_symbol`/`find_references`, git history tools (`git_log`, `git_blame`, `git_show`), web tools (`web_search`, `web_fetch`), and escalation to the deep model slot via a built-in `think_deeper` tool — so Pyrrhon can answer "what calls this?", "why did this change?", and multi-file architectural questions.

**Architecture:** Everything lands in `pyrrhon/core/` behind the existing `Tool` ABC and is registered in `build_agent`, so all channels (REPL, TUI, voice) get the new capabilities for free. The symbol index is the flagship real-time-discipline case from the spec: tree-sitter parsing and SQLite writes are CPU-bound, so every parse/DB operation runs via `asyncio.to_thread()` — a `ProcessPoolExecutor` swap-in is the M5+ optimization if cold-indexing a huge repo proves slow. This milestone indexes the **Python grammar only**; because `tree-sitter-language-pack` bundles 305+ pre-compiled grammars behind one `get_language(name)` call, adding more languages later is a config addition (a name→query-source mapping), not new architecture. Escalation is deliberately a *tool*, not a router: the fast model decides when a question deserves the deep model, keeping the voice path fast by default.

**Tech Stack:** Python ≥ 3.12, uv, existing M0 stack (pydantic v2, openai SDK, rich, pytest + pytest-asyncio + respx) plus: `tree-sitter` (py-tree-sitter bindings), `tree-sitter-language-pack` (pre-compiled grammars), `ddgs` (DuckDuckGo metasearch, no API key), `httpx` (already transitive; now a direct dependency), `html2text` (HTML → readable text).

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` (M4 section, "AST / code map", "Real-time discipline", the two model slots, and the amendment that git tools are ordinary tools, not part of citation verification).

## Research notes (verified 2026-07-03 via Context7)

- **py-tree-sitter** (`tree-sitter` on PyPI, `/tree-sitter/py-tree-sitter`): the current query API is `Query(language, source)` constructed directly, executed with `QueryCursor(query).captures(node)`, which returns `dict[capture_name, list[Node]]`. (`Language.query()` and `Query.captures()` from older releases are gone — do not use them.) Nodes expose `node.text` (bytes) and `node.start_point.row` (0-based).
- **tree-sitter-language-pack** (`/kreuzberg-dev/tree-sitter-language-pack`): `get_language(name: str) -> Language` and `get_parser(name: str) -> Parser` return ready-to-use objects for 305+ bundled grammars; `"python"` is the name used here.
- **ddgs** (`/deedy5/ddgs`): the maintained successor to the old `duckduckgo_search` package. `from ddgs import DDGS`; `DDGS().text(query, max_results=5)` returns `list[dict]` with keys `title`, `href`, `body`. Synchronous (blocking) — must be offloaded via `asyncio.to_thread()`.
- **html2text** (`/alir3z4/html2text`): `HTML2Text()` instance with `ignore_links`/`ignore_images`/`body_width` attributes; `.handle(html_str)` returns readable Markdown-ish text.

## Assumed from M1–M3 (treat as given — do not rebuild)

- **GroundingGate** (M1) sits between the agent and channels; these tools do not interact with it (git tools are explicitly *not* part of citation verification, per spec amendment).
- **RememberTool** (M1) is registered in `build_agent` — keep it in the tool list when editing that function.
- **Command registry** (M2): `command(name, help_text)` decorator + `dispatch(line, ctx)`. No new commands in M4.
- **Session** (M3): `Session(agent)` owning `history` and `abort_current_turn()`. Turn cancellation propagates into these tools automatically because every one of them awaits (`to_thread`, subprocess, httpx) — cancellation points already exist.

## Global Constraints

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.**
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then.
- No grounding *verification* in M0 (that is M1); M0 only extracts citations for files that exist.
- Commit after every task (green tests only).

**M4 additions:**

- Subprocesses are spawned with `asyncio.create_subprocess_exec` only — **never `shell=True`**, never string-concatenated commands.
- Network-touching tests (web search/fetch) never hit the real network: `respx` mocks httpx; a fake ddgs client is injected. Symbol-index and git tests run against copies of the fixture repo in `tmp_path` — never mutate `tests/fixtures/sample_repo` in place.
- The index database lives at `<root>/.pyrrhon/cache.db`; `.pyrrhon` is already in `SKIP_DIRS`, so repo tools and the index itself never scan it.

## File Structure (delta over M0–M3)

```text
pyrrhon/
├── repl.py                      # MODIFIED: build_agent registers M4 tools + deep slot
└── core/
    ├── agent/
    │   ├── loop.py              # MODIFIED: Agent(..., deep_llm=None) registers ThinkDeeperTool
    │   ├── prompts.py           # MODIFIED: + DEEP_SYSTEM_PROMPT, ESCALATION_NOTE
    │   └── escalate.py          # NEW: ThinkDeeperTool
    └── tools/
        ├── ast_index.py         # NEW: SymbolIndex, FindSymbolTool, FindReferencesTool
        ├── git.py               # NEW: GitLogTool, GitBlameTool, GitShowTool
        └── web.py               # NEW: WebSearchTool, WebFetchTool

tests/
├── test_symbol_index.py         # NEW
├── test_ast_tools.py            # NEW
├── test_git_tools.py            # NEW
├── test_web_tools.py            # NEW
├── test_escalation.py           # NEW
└── test_build_agent_m4.py       # NEW
```

---

### Task 1: SymbolIndex — tree-sitter parse → SQLite cache, mtime-invalidated

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `pyrrhon/core/tools/ast_index.py` (SymbolIndex only; tools come in Task 2)
- Test: `tests/test_symbol_index.py`

**Interfaces:**
- Consumes: `SKIP_DIRS` from `pyrrhon.core.tools.repo` (M0 Task 5).
- Produces:
  - `class SymbolIndex`: `__init__(self, root: Path)` — stores paths only, **no I/O in `__init__`** (so `build_agent` can construct it without touching disk)
  - SQLite DB at `<root>/.pyrrhon/cache.db` with tables `files(path TEXT PRIMARY KEY, mtime REAL)`, `symbols(name TEXT, kind TEXT, file TEXT, line INTEGER)`, `refs(name TEXT, file TEXT, line INTEGER)`
  - `async def ensure_fresh(self) -> None` — re-parses only files whose mtime changed; removes rows for deleted files; serialized by an `asyncio.Lock`; all parse + DB work inside `asyncio.to_thread`
  - `async def find_symbol(self, name: str) -> list[tuple[str, int, str]]` — `(file, line, kind)` rows, ordered by file then line
  - `async def find_references(self, name: str) -> list[tuple[str, int]]` — `(file, line)` rows, ordered by file then line

Definitions are captured with the tree-sitter query patterns
`(function_definition name: (identifier) @def.function)` and
`(class_definition name: (identifier) @def.class)`; references with
`(call function: (identifier) @ref)` and
`(call function: (attribute attribute: (identifier) @ref))` — i.e. "references"
in M4 means *call sites* (plain and method calls), which is exactly what
"what calls this?" needs. Python-only in this milestone; more languages later
are a per-language `(grammar name, query source)` config entry thanks to the
language pack.

- [ ] **Step 1: Add dependencies**

Run:

```bash
uv add tree-sitter tree-sitter-language-pack
```

Run: `uv sync` — Expected: resolves and installs without error (the language pack ships pre-compiled wheels; no compiler needed).

- [ ] **Step 2: Write the failing test**

`tests/test_symbol_index.py`:

```python
import os
import shutil
from pathlib import Path

import pytest

from pyrrhon.core.tools.ast_index import SymbolIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A disposable copy of the fixture repo — the index writes .pyrrhon/cache.db."""
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def _bump_mtime(path: Path) -> None:
    """Force a visibly newer mtime (filesystem mtime granularity can be coarse)."""
    stamp = path.stat().st_mtime + 10
    os.utime(path, (stamp, stamp))


async def test_init_does_no_io(repo: Path):
    SymbolIndex(repo)
    assert not (repo / ".pyrrhon").exists()


async def test_finds_function_definitions(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    assert await index.find_symbol("greet") == [("utils/helpers.py", 1, "function")]
    assert await index.find_symbol("main") == [("app.py", 4, "function")]
    assert (repo / ".pyrrhon" / "cache.db").is_file()


async def test_finds_call_references(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    assert await index.find_references("greet") == [("app.py", 5)]
    assert await index.find_references("main") == [("app.py", 9)]


async def test_unknown_symbol_returns_empty(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    assert await index.find_symbol("does_not_exist") == []
    assert await index.find_references("does_not_exist") == []


async def test_reindexes_only_changed_files(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    helpers = repo / "utils" / "helpers.py"
    helpers.write_text(
        'def greet(name: str) -> str:\n'
        '    return f"Hello, {name}!"\n'
        "\n"
        "\n"
        "def shout(name: str) -> str:\n"
        '    return f"HELLO, {name}!"\n',
        encoding="utf-8",
    )
    _bump_mtime(helpers)
    await index.ensure_fresh()
    assert await index.find_symbol("shout") == [("utils/helpers.py", 5, "function")]
    # Class definitions get kind "class":
    (repo / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    await index.ensure_fresh()
    assert await index.find_symbol("User") == [("models.py", 1, "class")]


async def test_deleted_files_drop_out_of_the_index(repo: Path):
    index = SymbolIndex(repo)
    await index.ensure_fresh()
    (repo / "app.py").unlink()
    await index.ensure_fresh()
    assert await index.find_symbol("main") == []
    assert await index.find_references("greet") == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_symbol_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.tools.ast_index'`

- [ ] **Step 4: Write minimal implementation**

`pyrrhon/core/tools/ast_index.py`:

```python
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

from tree_sitter import Query, QueryCursor
from tree_sitter_language_pack import get_language, get_parser

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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL);
CREATE TABLE IF NOT EXISTS symbols (name TEXT, kind TEXT, file TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS refs (name TEXT, file TEXT, line INTEGER);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols (name);
CREATE INDEX IF NOT EXISTS idx_refs_name ON refs (name);
"""


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
            parser = get_parser("python")  # per-call: Parser objects are not thread-safe
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
        conn.execute("INSERT OR REPLACE INTO files (path, mtime) VALUES (?, ?)", (rel, mtime))

    def _forget(self, conn: sqlite3.Connection, rel: str) -> None:
        conn.execute("DELETE FROM symbols WHERE file = ?", (rel,))
        conn.execute("DELETE FROM refs WHERE file = ?", (rel,))
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_symbol_index.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock pyrrhon/core/tools/ast_index.py tests/test_symbol_index.py
git commit -m "feat: tree-sitter symbol index with SQLite cache and mtime invalidation"
```

---

### Task 2: find_symbol + find_references tools

**Files:**
- Modify: `pyrrhon/core/tools/ast_index.py` (append the two tool classes)
- Test: `tests/test_ast_tools.py`

**Interfaces:**
- Consumes: `SymbolIndex` (Task 1), `Tool` ABC from `pyrrhon.core.tools.base` (M0 Task 5).
- Produces:
  - `FindSymbolTool(Tool)`: `name = "find_symbol"`, params `{name: str}` (required); `__init__(self, index: SymbolIndex)`; output lines `f"{path}:{line}: {kind} {name}"`; `"No matches."` when empty
  - `FindReferencesTool(Tool)`: `name = "find_references"`, params `{name: str}` (required); `__init__(self, index: SymbolIndex)`; output lines `f"{path}:{line}"`; `"No matches."` when empty
  - Both call `await self.index.ensure_fresh()` before querying, so results are never stale and no separate warm-up step exists.

- [ ] **Step 1: Write the failing test**

`tests/test_ast_tools.py`:

```python
import shutil
from pathlib import Path

import pytest

from pyrrhon.core.tools.ast_index import FindReferencesTool, FindSymbolTool, SymbolIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def index(tmp_path: Path) -> SymbolIndex:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return SymbolIndex(dest)


async def test_find_symbol_formats_path_line_kind_name(index: SymbolIndex):
    out = await FindSymbolTool(index).run(name="greet")
    assert out == "utils/helpers.py:1: function greet"


async def test_find_references_formats_path_line(index: SymbolIndex):
    out = await FindReferencesTool(index).run(name="greet")
    assert out == "app.py:5"


async def test_no_matches_message(index: SymbolIndex):
    assert await FindSymbolTool(index).run(name="ghost") == "No matches."
    assert await FindReferencesTool(index).run(name="ghost") == "No matches."


async def test_schemas(index: SymbolIndex):
    sym = FindSymbolTool(index).schema()
    ref = FindReferencesTool(index).schema()
    assert sym["function"]["name"] == "find_symbol"
    assert ref["function"]["name"] == "find_references"
    for schema in (sym, ref):
        assert schema["function"]["parameters"]["required"] == ["name"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ast_tools.py -v`
Expected: FAIL with `ImportError: cannot import name 'FindSymbolTool'`

- [ ] **Step 3: Write minimal implementation**

Append to `pyrrhon/core/tools/ast_index.py` (below `SymbolIndex`; also add `from pyrrhon.core.tools.base import Tool` to the imports at the top of the file):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ast_tools.py tests/test_symbol_index.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/ast_index.py tests/test_ast_tools.py
git commit -m "feat: find_symbol and find_references tools over the symbol index"
```

---

### Task 3: Git history tools — git_log, git_blame, git_show

**Files:**
- Create: `pyrrhon/core/tools/git.py`
- Test: `tests/test_git_tools.py`

**Interfaces:**
- Consumes: `Tool` ABC (M0 Task 5), `_resolve_inside` from `pyrrhon.core.tools.repo` (M0 Task 5 — path-sandbox helper; if M1–M3 renamed or moved it, use the current equivalent).
- Produces (all `__init__(self, root: Path)`; all run `git` via `asyncio.create_subprocess_exec(..., cwd=root)` — **never `shell=True`**; non-zero exit → `"ERROR: <stderr>"`):
  - `GitLogTool`: `name = "git_log"`, params `{path?: str, max_count?: int}` (default 20, clamped to 1–100); output one commit per line: `<short-hash> <date> <author> <subject>`
  - `GitBlameTool`: `name = "git_blame"`, params `{path: str, start_line?: int, end_line?: int}`; `-L start,end` when lines given (`end_line` defaults to `start_line`)
  - `GitShowTool`: `name = "git_show"`, params `{ref: str}`; refs starting with `-` are rejected (option-injection guard)
  - Module constant `MAX_GIT_OUTPUT = 8000`; longer stdout is truncated with a `\n(truncated)` suffix

- [ ] **Step 1: Write the failing test**

`tests/test_git_tools.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Fixture repo turned into a real git repo with two commits."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    _git(repo, "init")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add sample app")
    (repo / "utils" / "helpers.py").write_text(
        'def greet(name: str) -> str:\n    return f"Hi, {name}!"\n', encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "change greeting")
    return repo


async def test_git_log_lists_both_commits(git_repo: Path):
    out = await GitLogTool(git_repo).run()
    assert "change greeting" in out
    assert "add sample app" in out
    assert "Test" in out


async def test_git_log_respects_max_count_and_path_filter(git_repo: Path):
    only_latest = await GitLogTool(git_repo).run(max_count=1)
    assert "change greeting" in only_latest
    assert "add sample app" not in only_latest
    readme_only = await GitLogTool(git_repo).run(path="README.md")
    assert "add sample app" in readme_only
    assert "change greeting" not in readme_only


async def test_git_log_rejects_path_escape(git_repo: Path):
    out = await GitLogTool(git_repo).run(path="../outside.txt")
    assert out.startswith("ERROR:")


async def test_git_blame_shows_author_and_line_range(git_repo: Path):
    out = await GitBlameTool(git_repo).run(path="utils/helpers.py", start_line=2, end_line=2)
    assert "Hi, {name}!" in out
    assert "Test" in out
    assert "def greet" not in out  # -L 2,2 excludes line 1


async def test_git_show_head_includes_message_and_diff(git_repo: Path):
    out = await GitShowTool(git_repo).run(ref="HEAD")
    assert "change greeting" in out
    assert '+    return f"Hi, {name}!"' in out


async def test_git_show_rejects_option_like_refs(git_repo: Path):
    assert (await GitShowTool(git_repo).run(ref="--help")).startswith("ERROR:")
    assert (await GitShowTool(git_repo).run(ref="")).startswith("ERROR:")


async def test_not_a_git_repo_is_an_error_string(tmp_path: Path):
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    out = await GitLogTool(bare).run()
    assert out.startswith("ERROR:")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_git_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.tools.git'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/tools/git.py`:

```python
"""Git history tools: log, blame, show.

Ordinary tools for history-aware questions — explicitly NOT part of citation
verification (spec amendment 2026-07-03). All subprocess calls use
asyncio.create_subprocess_exec (argv list, cwd=root); never shell=True, so no
quoting/injection surface. Subprocess awaits are natural cancellation points
for M3's abort_current_turn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.repo import _resolve_inside

MAX_GIT_OUTPUT = 8000


async def _run_git(root: Path, *args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as exc:
        return f"ERROR: could not run git: {exc}"
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        return f"ERROR: {detail or f'git exited with code {proc.returncode}'}"
    text = stdout.decode("utf-8", errors="replace")
    if len(text) > MAX_GIT_OUTPUT:
        text = text[:MAX_GIT_OUTPUT] + "\n(truncated)"
    return text or "(no output)"


class GitLogTool(Tool):
    name = "git_log"
    description = (
        "Show recent commit history (short hash, date, author, subject), most "
        "recent first — optionally filtered to one file or directory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional repo-relative path to filter history",
            },
            "max_count": {
                "type": "integer",
                "description": "How many commits to show (default 20, max 100)",
            },
        },
        "required": [],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, path: str | None = None, max_count: int = 20) -> str:
        max_count = max(1, min(int(max_count), 100))
        args = [
            "log",
            f"--max-count={max_count}",
            "--date=short",
            "--format=%h %ad %an %s",
        ]
        if path:
            if _resolve_inside(self.root, path) is None:
                return f"ERROR: '{path}' is outside the repo."
            args += ["--", path]
        return await _run_git(self.root, *args)


class GitBlameTool(Tool):
    name = "git_blame"
    description = (
        "Show who last changed each line of a file (and when). Use start_line/"
        "end_line to blame just a range."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path"},
            "start_line": {"type": "integer", "description": "1-based first line"},
            "end_line": {
                "type": "integer",
                "description": "1-based last line, inclusive (defaults to start_line)",
            },
        },
        "required": ["path"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(
        self, path: str, start_line: int | None = None, end_line: int | None = None
    ) -> str:
        if _resolve_inside(self.root, path) is None:
            return f"ERROR: '{path}' is outside the repo."
        args = ["blame", "--date=short"]
        if start_line is not None:
            args += ["-L", f"{start_line},{end_line or start_line}"]
        args += ["--", path]
        return await _run_git(self.root, *args)


class GitShowTool(Tool):
    name = "git_show"
    description = (
        "Show a commit: message, author, and full diff. Pass a ref like a short "
        "hash from git_log, or HEAD, or HEAD~2."
    )
    parameters = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "Commit ref, e.g. 'a1b2c3d' or 'HEAD~1'"},
        },
        "required": ["ref"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, ref: str) -> str:
        ref = ref.strip()
        if not ref or ref.startswith("-"):
            return f"ERROR: invalid ref '{ref}'."
        return await _run_git(self.root, "show", ref)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_git_tools.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/git.py tests/test_git_tools.py
git commit -m "feat: git history tools (git_log, git_blame, git_show)"
```

---

### Task 4: Web tools — web_search (ddgs) + web_fetch (httpx + html2text)

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `pyrrhon/core/tools/web.py`
- Test: `tests/test_web_tools.py`

**Interfaces:**
- Consumes: `Tool` ABC (M0 Task 5).
- Produces:
  - `WebSearchTool(Tool)`: `name = "web_search"`, params `{query: str, max_results?: int}` (default 5, clamped 1–10); `__init__(self, client=None)` — injectable ddgs-shaped client for tests, defaults to a lazily created `ddgs.DDGS()`; output is `"title — url\nsnippet"` blocks separated by blank lines; `"No results."` when empty; ddgs is synchronous so the call is offloaded via `asyncio.to_thread`
  - `WebFetchTool(Tool)`: `name = "web_fetch"`, params `{url: str}` (required); httpx async GET, `timeout=15.0`, `follow_redirects=True`; only `http://`/`https://` URLs — anything else → `ERROR:` string; HTML responses stripped to readable text via `html2text` (CPU work offloaded via `asyncio.to_thread`); output capped at `MAX_FETCH_CHARS = 8000` with `\n(truncated)` suffix; HTTP ≥ 400 and transport errors → `ERROR:` strings

- [ ] **Step 1: Add dependencies**

Run:

```bash
uv add ddgs httpx html2text
```

(`httpx` was already a transitive dependency via `openai`/`respx`; it becomes a direct one because `web.py` imports it.)

Run: `uv sync` — Expected: resolves and installs without error.

- [ ] **Step 2: Write the failing test**

`tests/test_web_tools.py`:

```python
import httpx
import respx

from pyrrhon.core.tools.web import MAX_FETCH_CHARS, WebFetchTool, WebSearchTool


class FakeDDGS:
    """ddgs.DDGS stand-in: same .text(query, max_results=...) shape."""

    def __init__(self, results=None, error=None):
        self._results = results or []
        self._error = error
        self.calls: list[tuple[str, int]] = []

    def text(self, query: str, max_results: int = 10):
        self.calls.append((query, max_results))
        if self._error is not None:
            raise self._error
        return self._results[:max_results]


RESULTS = [
    {
        "title": "asyncio — Asynchronous I/O",
        "href": "https://docs.python.org/3/library/asyncio.html",
        "body": "asyncio is a library to write concurrent code.",
    },
    {
        "title": "Real Python: Async IO",
        "href": "https://realpython.com/async-io-python/",
        "body": "A complete walkthrough of async IO in Python.",
    },
]


async def test_search_formats_title_url_snippet_blocks():
    tool = WebSearchTool(client=FakeDDGS(results=RESULTS))
    out = await tool.run(query="python asyncio")
    blocks = out.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0] == (
        "asyncio — Asynchronous I/O — https://docs.python.org/3/library/asyncio.html\n"
        "asyncio is a library to write concurrent code."
    )


async def test_search_passes_clamped_max_results():
    fake = FakeDDGS(results=RESULTS)
    await WebSearchTool(client=fake).run(query="q", max_results=99)
    assert fake.calls == [("q", 10)]  # clamped to 10


async def test_search_empty_and_error_paths():
    assert await WebSearchTool(client=FakeDDGS()).run(query="q") == "No results."
    failing = FakeDDGS(error=RuntimeError("rate limited"))
    out = await WebSearchTool(client=failing).run(query="q")
    assert out.startswith("ERROR:")
    assert "rate limited" in out


@respx.mock
async def test_fetch_strips_html_to_text():
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(
            200,
            text="<html><body><h1>Title</h1><p>Hello <b>world</b>.</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    out = await WebFetchTool().run(url="https://example.com/page")
    assert "Title" in out
    assert "Hello" in out and "world" in out
    assert "<h1>" not in out and "<b>" not in out


@respx.mock
async def test_fetch_caps_output_length():
    respx.get("https://example.com/big").mock(
        return_value=httpx.Response(
            200, text="x" * 20000, headers={"content-type": "text/plain"}
        )
    )
    out = await WebFetchTool().run(url="https://example.com/big")
    assert out.endswith("(truncated)")
    assert len(out) <= MAX_FETCH_CHARS + len("\n(truncated)")


@respx.mock
async def test_fetch_http_error_is_error_string():
    respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    out = await WebFetchTool().run(url="https://example.com/missing")
    assert out.startswith("ERROR:")
    assert "404" in out


async def test_fetch_rejects_non_http_urls():
    tool = WebFetchTool()
    assert (await tool.run(url="file:///etc/passwd")).startswith("ERROR:")
    assert (await tool.run(url="ftp://example.com/x")).startswith("ERROR:")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_web_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.tools.web'`

- [ ] **Step 4: Write minimal implementation**

`pyrrhon/core/tools/web.py`:

```python
"""Web tools: DuckDuckGo search (ddgs, keyless) and page fetch (httpx + html2text).

Real-time discipline: ddgs is a synchronous client, so the search call runs in
asyncio.to_thread(); html2text conversion (CPU-bound on large pages) is
offloaded the same way. The httpx GET is natively async.
"""

from __future__ import annotations

import asyncio

import html2text
import httpx
from ddgs import DDGS

from pyrrhon.core.tools.base import Tool

MAX_FETCH_CHARS = 8000
MAX_SEARCH_RESULTS = 10
FETCH_TIMEOUT_SECONDS = 15.0


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web (DuckDuckGo). Use for library docs, error messages, and "
        "facts that are not in the repo. Returns title, URL, and snippet per result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": "How many results (default 5, max 10)",
            },
        },
        "required": ["query"],
    }

    def __init__(self, client=None):
        self._client = client  # tests inject a fake; None → real DDGS, created lazily

    async def run(self, query: str, max_results: int = 5) -> str:
        max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
        return await asyncio.to_thread(self._search, query, max_results)

    def _search(self, query: str, max_results: int) -> str:
        client = self._client or DDGS()
        try:
            results = client.text(query, max_results=max_results)
        except Exception as exc:  # ddgs raises assorted backend errors
            return f"ERROR: web search failed: {exc}"
        if not results:
            return "No results."
        blocks = [
            f"{r['title']} — {r['href']}\n{r['body']}" for r in results
        ]
        return "\n\n".join(blocks)


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch a web page and return its readable text (HTML is stripped). "
        "Only http(s) URLs. Output is capped, so fetch specific pages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full http(s) URL to fetch"},
        },
        "required": ["url"],
    }

    async def run(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return f"ERROR: only http(s) URLs are supported, got '{url}'."
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return f"ERROR: fetch failed: {exc}"
        if response.status_code >= 400:
            return f"ERROR: HTTP {response.status_code} for {url}"
        text = response.text
        if "html" in response.headers.get("content-type", ""):
            text = await asyncio.to_thread(_strip_html, text)
        text = text.strip()
        if len(text) > MAX_FETCH_CHARS:
            text = text[:MAX_FETCH_CHARS] + "\n(truncated)"
        return text or "(empty page)"


def _strip_html(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.body_width = 0  # no hard wrapping — speakable prose stays intact
    return converter.handle(html)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_web_tools.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock pyrrhon/core/tools/web.py tests/test_web_tools.py
git commit -m "feat: web_search (ddgs) and web_fetch (httpx + html2text) tools"
```

---

### Task 5: Deep-model escalation — think_deeper tool + Agent deep_llm kwarg

**Files:**
- Create: `pyrrhon/core/agent/escalate.py`
- Modify: `pyrrhon/core/agent/prompts.py` (append two constants), `pyrrhon/core/agent/loop.py` (`Agent.__init__` gains `deep_llm=None`)
- Test: `tests/test_escalation.py`

**Interfaces:**
- Consumes: `Tool` ABC (M0 Task 5), `Agent` (M0 Task 8 — `__init__(llm, tools, system_prompt, repo_root, max_tool_rounds=8)`; revalidate against the post-M3 signature per the drift warning), `FakeLLM`/`LLMReply`/`ToolCall` (M0 Tasks 3–4, tests only).
- Produces:
  - `prompts.DEEP_SYSTEM_PROMPT: str` — system prompt for the deep model (grounding-aware: cite only locations present in the provided context)
  - `prompts.ESCALATION_NOTE: str` — appended to the *fast* model's system prompt when `think_deeper` is available; tells it to escalate multi-file architectural analysis
  - `escalate.ThinkDeeperTool(Tool)`: `name = "think_deeper"`, params `{question: str, context: str}` (both required); `__init__(self, deep_llm)` where `deep_llm` is anything with `async chat(messages, tools=None) -> LLMReply`; sends `DEEP_SYSTEM_PROMPT` + question + context to the deep model and returns its text; failures → `ERROR:` strings
  - `Agent.__init__(..., deep_llm=None)`: when `deep_llm` is not `None`, the agent registers `ThinkDeeperTool(deep_llm)` into `self.tools` and appends `ESCALATION_NOTE` to its system prompt; when `None`, nothing changes — the tool simply does not exist

Design rationale (pinned): escalation is a **tool the fast model calls**, not a
pre-turn router. The fast model stays the conversational voice (latency), and
the deep model is a consultant it hands a dossier to — `question` plus the
`context` it already gathered with repo/ast/git tools. The deep model's reply
comes back as an ordinary tool result, so the fast model narrates it and the
grounding gate (M1) still vets whatever is finally spoken.

- [ ] **Step 1: Write the failing test**

`tests/test_escalation.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.escalate import ThinkDeeperTool
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.prompts import DEEP_SYSTEM_PROMPT, ESCALATION_NOTE
from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_think_deeper_sends_prompt_question_and_context():
    deep = FakeLLM([LLMReply(text="The layering is clean: app -> utils.")])
    tool = ThinkDeeperTool(deep)
    out = await tool.run(
        question="How do the layers interact?",
        context="app.py:1 imports greet from utils/helpers.py:1",
    )
    assert out == "The layering is clean: app -> utils."
    messages = deep.calls[0]["messages"]
    assert messages[0] == {"role": "system", "content": DEEP_SYSTEM_PROMPT}
    assert "How do the layers interact?" in messages[1]["content"]
    assert "app.py:1 imports greet" in messages[1]["content"]
    assert deep.calls[0]["tools"] is None  # the deep model gets no tools — it reasons over the dossier


async def test_think_deeper_error_paths():
    class ExplodingLLM:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    assert (await ThinkDeeperTool(ExplodingLLM()).run(question="q", context="c")).startswith(
        "ERROR:"
    )
    empty = FakeLLM([LLMReply(text=None)])
    assert (await ThinkDeeperTool(empty).run(question="q", context="c")).startswith("ERROR:")


def test_agent_registers_tool_and_note_only_with_deep_llm():
    with_deep = Agent(
        llm=FakeLLM([]),
        tools=[],
        system_prompt="base prompt",
        repo_root=FIXTURE,
        deep_llm=FakeLLM([]),
    )
    assert "think_deeper" in with_deep.tools
    assert ESCALATION_NOTE in with_deep.system_prompt

    without = Agent(
        llm=FakeLLM([]), tools=[], system_prompt="base prompt", repo_root=FIXTURE
    )
    assert "think_deeper" not in without.tools
    assert without.system_prompt == "base prompt"


async def test_full_turn_escalates_through_think_deeper():
    deep = FakeLLM([LLMReply(text="Deep analysis: greet is the only seam between files.")])
    fast = FakeLLM(
        [
            LLMReply(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="think_deeper",
                        arguments={
                            "question": "What breaks if greet changes?",
                            "context": "greet defined utils/helpers.py:1, called app.py:5",
                        },
                    ),
                )
            ),
            LLMReply(text="Changing greet only affects app.py:5."),
        ]
    )
    agent = Agent(
        llm=fast, tools=[], system_prompt="base", repo_root=FIXTURE, deep_llm=deep
    )
    events = [event async for event in agent.run_turn([], "what breaks if greet changes?")]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert "Deep analysis" in finished[0].result_preview
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "Changing greet only affects app.py:5."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_escalation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.agent.escalate'`

- [ ] **Step 3: Write minimal implementation**

Append to `pyrrhon/core/agent/prompts.py`:

```python
DEEP_SYSTEM_PROMPT = """\
You are the deep-reasoning half of Pyrrhon, a senior engineer's engineer.
A faster conversational model has gathered code excerpts, symbol locations,
and history for you. Your job is the hard part: multi-file architectural
analysis — how a change here propagates there, why the design is shaped this
way, what the alternatives and trade-offs are.

Rules:
- Reason only over the provided context. Cite path:line locations ONLY when
  they appear in the context you were given — never invent locations.
- If the context is insufficient, say exactly which files, symbols, or history
  you need next; the fast model will fetch them and ask again.
- Be dense and structured: conclusions first, then the reasoning chain.
"""

ESCALATION_NOTE = """\
You also have a think_deeper tool backed by a stronger reasoning model. Call it
for multi-file architectural analysis: "map how X affects Y", impact-of-change
questions spanning several files, design trade-off evaluations, or anything you
have gathered evidence for but cannot confidently synthesize. First collect the
relevant code with your other tools, then pass the question plus everything you
found (code excerpts, path:line locations, git findings) as `context` — the
deep model sees only what you hand it. Do not escalate simple lookups.
"""
```

`pyrrhon/core/agent/escalate.py`:

```python
"""Deep-model escalation: the fast model consults the deep slot via a tool.

Escalation is a tool, not a router — the fast model stays the low-latency
voice and decides when a question deserves the deep model (spec: two model
slots; Settings.deep_slot falls back to fast when unset).
"""

from __future__ import annotations

from pyrrhon.core.agent.prompts import DEEP_SYSTEM_PROMPT
from pyrrhon.core.tools.base import Tool


class ThinkDeeperTool(Tool):
    name = "think_deeper"
    description = (
        "Consult the deep reasoning model for multi-file architectural analysis. "
        "Pass the question AND all evidence you gathered (code excerpts, path:line "
        "locations, git history) as `context` — the deep model sees only that."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The hard question to analyze",
            },
            "context": {
                "type": "string",
                "description": "Code excerpts, path:line locations, and findings gathered so far",
            },
        },
        "required": ["question", "context"],
    }

    def __init__(self, deep_llm):
        self.deep_llm = deep_llm  # anything with async chat(messages, tools=None) -> LLMReply

    async def run(self, question: str, context: str) -> str:
        messages = [
            {"role": "system", "content": DEEP_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{question}\n\n# Context gathered by the fast model\n\n{context}",
            },
        ]
        try:
            reply = await self.deep_llm.chat(messages)
        except Exception as exc:  # provider/network failure must not kill the turn
            return f"ERROR: deep model call failed: {exc}"
        return reply.text or "ERROR: deep model returned no text."
```

In `pyrrhon/core/agent/loop.py`, change `Agent.__init__` to (add the two imports; everything else in the file is untouched):

```python
from pyrrhon.core.agent.escalate import ThinkDeeperTool
from pyrrhon.core.agent.prompts import ESCALATION_NOTE
```

```python
    def __init__(
        self,
        llm,
        tools: list[Tool],
        system_prompt: str,
        repo_root: Path,
        max_tool_rounds: int = 8,
        deep_llm=None,
    ):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        self.system_prompt = system_prompt
        self.repo_root = repo_root
        self.max_tool_rounds = max_tool_rounds
        if deep_llm is not None:
            deep_tool = ThinkDeeperTool(deep_llm)
            self.tools[deep_tool.name] = deep_tool
            self.system_prompt = system_prompt + "\n" + ESCALATION_NOTE
```

(Drift check: if M1–M3 added parameters to `Agent.__init__` — e.g. a grounding
gate — append `deep_llm=None` after them and keep their wiring intact; only the
final `if deep_llm is not None:` block is new.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_escalation.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole suite (the Agent signature changed)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent tests/test_escalation.py
git commit -m "feat: think_deeper escalation tool wired to the deep model slot"
```

---

### Task 6: Wire everything into build_agent + docs

**Files:**
- Modify: `pyrrhon/repl.py` (`build_agent`), `CLAUDE.md` (current-state line)
- Test: `tests/test_build_agent_m4.py`

**Interfaces:**
- Consumes: `Settings.deep_slot` + `load_settings` (M0 Task 2), `create_llm`/`MissingAPIKeyError` (M0 Task 3), all M4 tools (Tasks 1–4), `Agent(..., deep_llm=...)` (Task 5), `build_agent` (M0 Task 9 — **revalidate**: M1–M3 added at least `RememberTool` and grounding-gate wiring to this function; merge the M4 additions in, do not replace their work).
- Produces: `build_agent(repo_root: Path, llm=None, deep_llm=None) -> Agent` — registers `find_symbol`, `find_references`, `git_log`, `git_blame`, `git_show`, `web_search`, `web_fetch` alongside the existing tools; wires `deep_llm=create_llm(settings.deep_slot, settings)` when that slot's API key exists, else `None` (so `think_deeper` is simply not registered). Note the fallback chain: `Settings.deep_slot` already falls back to `fast` when `deep` is unset, so with only one key configured the deep consultant is the fast model — allowed by spec, and the tool still shapes *how* the model reasons (dossier in, analysis out).

- [ ] **Step 1: Write the failing test**

`tests/test_build_agent_m4.py`:

```python
from pathlib import Path

from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

M4_TOOLS = {
    "find_symbol",
    "find_references",
    "git_log",
    "git_blame",
    "git_show",
    "web_search",
    "web_fetch",
}


def test_build_agent_registers_m4_tools_without_deep_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    assert M4_TOOLS <= set(agent.tools)
    assert {"read_file", "grep", "glob"} <= set(agent.tools)  # M0 tools still there
    # deep_slot falls back to fast (groq); no key -> no escalation tool:
    assert "think_deeper" not in agent.tools


def test_build_agent_wires_deep_slot_when_key_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    assert "think_deeper" in agent.tools


def test_build_agent_construction_does_no_index_io(monkeypatch, tmp_path):
    # SymbolIndex.__init__ is I/O-free, so building an agent must not create
    # .pyrrhon/cache.db — the index is built lazily on first tool use.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    build_agent(repo, llm=FakeLLM([]))
    assert not (repo / ".pyrrhon" / "cache.db").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_agent_m4.py -v`
Expected: FAIL — `M4_TOOLS <= set(agent.tools)` assertion fails (new tools not registered yet)

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/repl.py`, add the imports:

```python
from pyrrhon.core.tools.ast_index import FindReferencesTool, FindSymbolTool, SymbolIndex
from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool
from pyrrhon.core.tools.web import WebFetchTool, WebSearchTool
```

and replace `build_agent` with (drift check: keep every tool and kwarg M1–M3
added to this function — e.g. `RememberTool`, grounding-gate wiring — the M4
delta is the new imports, the `deep_llm` resolution block, the seven new tool
registrations, and passing `deep_llm=` through):

```python
def build_agent(repo_root: Path, llm=None, deep_llm=None) -> Agent:
    settings = load_settings(repo_root)
    llm = llm or create_llm(settings.fast, settings)
    if deep_llm is None:
        try:
            # deep_slot falls back to fast when [deep] is unset (Settings rule).
            deep_llm = create_llm(settings.deep_slot, settings)
        except MissingAPIKeyError:
            deep_llm = None  # no key for the deep slot -> think_deeper not registered
    index = SymbolIndex(repo_root)
    tools = [
        ReadFileTool(repo_root),
        GrepTool(repo_root),
        GlobTool(repo_root),
        FindSymbolTool(index),
        FindReferencesTool(index),
        GitLogTool(repo_root),
        GitBlameTool(repo_root),
        GitShowTool(repo_root),
        WebSearchTool(),
        WebFetchTool(),
        # M1-M3 additions (e.g. RememberTool) stay in this list — merge, don't drop.
    ]
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=build_system_prompt(repo_root),
        repo_root=repo_root,
        deep_llm=deep_llm,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_build_agent_m4.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (M0–M3 suites plus the 31 M4 tests from Tasks 1–6)

- [ ] **Step 6: Manual smoke test (needs GROQ_API_KEY set)**

Run: `uv run pyrrhon .` against this repo and confirm:
- "where is build_agent defined and what calls it?" → `find_symbol`/`find_references` tool calls appear, answer cites `pyrrhon/repl.py:<line>`.
- "when did prompts.py last change and why?" → `git_log`/`git_show` calls appear, answer quotes a real commit subject.
- "what does the ddgs library do?" → `web_search`/`web_fetch` calls appear.
- With a second provider key configured for `[deep]` in `.pyrrhon.toml`, an architectural question ("map how a tool result flows from Tool.run to the screen") triggers a visible `think_deeper` call.
- Ask two questions in a row: the second symbol lookup is visibly faster (warm `.pyrrhon/cache.db`).

- [ ] **Step 7: Update CLAUDE.md current-state line**

In `CLAUDE.md`, update the current-state reference (exact preexisting wording
depends on M1–M3 — find the line naming the latest milestone plan) to:

```markdown
Current state: M4 (deep reasoning: tree-sitter symbol index, git history
tools, web search/fetch, think_deeper escalation) — see
`docs/superpowers/plans/2026-07-03-pyrrhon-m4-deep-reasoning-tools.md`.
```

- [ ] **Step 8: Commit**

```bash
git add pyrrhon/repl.py tests/test_build_agent_m4.py CLAUDE.md
git commit -m "feat: register M4 tools and deep-model escalation in build_agent"
```

---

## Definition of Done (M4)

- `uv run pytest` fully green (all prior suites plus the six new M4 test files).
- Against a real repo, Pyrrhon answers "where is X defined?" / "what calls X?" via the symbol index with `path:line` output, and repeated queries reuse `.pyrrhon/cache.db` (only mtime-changed files are re-parsed).
- "Why did this file change?" questions produce `git_log`/`git_blame`/`git_show` tool calls; a non-git directory or bad ref yields an `ERROR:` string the model can recover from, never a traceback.
- `web_search` returns titled, linked snippets with no API key configured; `web_fetch` returns readable text (no raw HTML tags) capped at 8000 chars, and rejects non-http(s) URLs.
- With a deep-slot key present, `think_deeper` is registered and a multi-file architectural question routes through it; with no key, the tool is absent and the agent still answers with its other tools.
- Real-time discipline holds: every new CPU/filesystem-bound path (tree-sitter parse, SQLite reads/writes, ddgs call, html2text conversion) runs under `asyncio.to_thread`; subprocesses use `create_subprocess_exec` (no `shell=True`); nothing blocks the event loop.
- `core/` still has no channel imports (verify: `grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/` returns nothing).
