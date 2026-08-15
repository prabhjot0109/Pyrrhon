# Pyrrhon M14 — Code Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver what M10 Stage 3 deferred — a table-driven multi-language symbol index (TypeScript/JavaScript + Go alongside Python), a `symbol_context` tool that answers "how does X work" in one tool round instead of three, a repo map that knows what the conversation is about, an orientation brief for an unfamiliar repo, and the eval that proves the quality claim.

**Architecture:** The blocking item goes first: `ast_index.py` hardcodes `.py` in its file walk and holds one module-level grammar plus three module-level queries, so adding a grammar today means a new grammar that never sees a file. A `LanguageSpec` table (extension → grammar → def/ref/import queries → an import-text parser) replaces all of it, with grammars compiled lazily on first use so a Python-only repo never pays for the Go grammar. On top of that table, `symbol_context` folds `find_symbol` + `find_references` + `list_dependencies` into one call that also returns the source window, removing two round trips from the commonest Understand-act question. It retires `find_references` from the belt (same `name` argument, superset output) but *not* `list_dependencies`, which is path-addressed and answers questions no symbol name reaches — see Task 4's amendment and Appendix A. The repo map gains a conversation-mention boost, and the orientation brief becomes the first real emitter of `ScreenArtifact`, an event type that has existed unused since M0.

**Tech Stack:** Python ≥3.12, uv, tree-sitter (<0.26, ABI-pinned), tree-sitter-language-pack, SQLite, pytest (asyncio_mode=auto).

## Global Constraints

- Python `>=3.12`; manage deps only via `uv add` / `uv sync`.
- **`tree-sitter>=0.25.2,<0.26` stays pinned.** 0.26.0 broke the C-ABI the pre-compiled grammars are linked against and segfaults `SymbolIndex.ensure_fresh` (`pyproject.toml:24-28`). Never relax this pin to get a newer grammar.
- **Grammar node names are version-specific.** Every query in this plan MUST be verified against the installed grammar by the verification step in its task — a query that compiles can still capture nothing. Do not assume the node names written here are correct for your installed version; the task tells you how to check.
- **Grounding is a hard requirement** (CLAUDE.md), and from M13 every tool must feed the `EvidenceLedger`. A new tool that returns `path:line` without recording evidence will have its citations downgraded — register it in `evidence.py` in the same task that adds it.
- **A cloned repo is untrusted input** (M11): parsing a repo file must never execute anything from it.
- **Real-time discipline** (`ast_index.py:1-11`): all parsing and SQLite work stays inside `asyncio.to_thread`.
- All M11–M13 tests stay green; `ruff` and `mypy pyrrhon/core` stay clean.
- Commit after every task with a conventional-commit message; never `--no-verify`.
- **Parked, do not build:** LSP integration, a call graph with edge direction, cross-language import resolution (a TS file importing a Python module), incremental parsing via tree-sitter edits, and any language beyond TS/JS/Go. Each is a defensible next step and none is this milestone.

## File Structure

| File | Responsibility |
|---|---|
| `pyrrhon/core/tools/languages.py` (create) | `LanguageSpec` table: extensions, grammar name, queries, import parser; lazy compilation |
| `pyrrhon/core/tools/ast_index.py` (modify) | Walk every indexable extension; parse via the table; store a `lang` column |
| `pyrrhon/core/tools/symbol_context.py` (create) | The one-round `symbol_context` tool |
| `pyrrhon/core/grounding/evidence.py` (modify) | Record evidence from `symbol_context` output |
| `pyrrhon/core/tools/orientation.py` (create) | Orientation brief; emits `ScreenArtifact` |
| `pyrrhon/repl.py` (modify) | Belt composition: `symbol_context` in, two tools out |
| `pyrrhon/core/agent/loop.py` (modify) | Feed conversation mentions to the repo map |
| `tests/fixtures/polyglot_repo/` (create) | Small TS, JS, and Go files with known symbols |
| `tests/test_languages.py` (create) | Table + query capture coverage |
| `tests/test_multilang_index.py` (create) | Indexing TS/JS/Go end to end |
| `tests/test_symbol_context.py` (create) | The one-round tool |
| `tests/test_orientation.py` (create) | Brief content and event type |
| `tests/test_safety.py` (modify) | The belt changes — a reviewed change, not a test edit of convenience |
| `evals/understanding.yaml` (create) | The quality claim, measured |

---

### Task 1: The language table

**Files:**
- Create: `pyrrhon/core/tools/languages.py`
- Test: `tests/test_languages.py`
- Create: `tests/fixtures/polyglot_repo/{app.ts,helpers.js,server.go}`

**Interfaces:**
- Consumes: `tree_sitter.Query`, `tree_sitter_language_pack.get_language`.
- Produces: `LanguageSpec(name, extensions, def_query, ref_query, import_query, parse_imports)`; `LANGUAGES: tuple[LanguageSpec, ...]`; `spec_for_extension(ext: str) -> LanguageSpec | None`; `INDEXABLE_EXTENSIONS: frozenset[str]`; `compiled(spec) -> CompiledLanguage` with `.language`, `.defs`, `.refs`, `.imports` (cached).

**Why this is first:** M10's own postscript says it outright —
"`ast_index.py:_iter_files_with_mtime` hardcodes `.py`; the extension set must
become table-driven in the same change, or new grammars will never see a file."

- [x] **Step 1: Create the fixture repo**

```typescript
// tests/fixtures/polyglot_repo/app.ts
import { formatName } from "./helpers.js";

export class Greeter {
  greet(name: string): string {
    return formatName(name);
  }
}

export function main(): void {
  new Greeter().greet("world");
}
```

```javascript
// tests/fixtures/polyglot_repo/helpers.js
export function formatName(name) {
  return `hello ${name}`;
}
```

```go
// tests/fixtures/polyglot_repo/server.go
package main

import "fmt"

type Server struct{ port int }

func (s *Server) Start() {
	fmt.Println(s.port)
}

func main() {
	Start()
}
```

Add `tests/fixtures/polyglot_repo/` to the fixture fence in `tests/conftest.py`
alongside `sample_repo` — M10 added that fence because indexing a checked-in
fixture writes a `cache.db` that survives into later runs.

- [x] **Step 2: Write the failing test**

```python
# tests/test_languages.py
"""The language table. Every query here is verified by CAPTURE, not by
compiling: a query with a node name that does not exist in the installed
grammar version compiles fine and silently captures nothing, which would show
up as 'the index is empty' three tasks later."""

from pathlib import Path

import pytest
from tree_sitter import Parser, QueryCursor

from pyrrhon.core.tools.languages import (
    INDEXABLE_EXTENSIONS,
    LANGUAGES,
    compiled,
    spec_for_extension,
)

FIXTURES = Path(__file__).parent / "fixtures" / "polyglot_repo"


def test_python_typescript_javascript_and_go_are_all_in_the_table():
    assert {spec.name for spec in LANGUAGES} >= {"python", "typescript", "javascript", "go"}


def test_extensions_map_to_specs():
    assert spec_for_extension(".py").name == "python"
    assert spec_for_extension(".ts").name == "typescript"
    assert spec_for_extension(".js").name == "javascript"
    assert spec_for_extension(".go").name == "go"
    assert spec_for_extension(".md") is None
    assert ".py" in INDEXABLE_EXTENSIONS and ".md" not in INDEXABLE_EXTENSIONS


def _captures(spec, source: bytes, which: str) -> set[str]:
    unit = compiled(spec)
    tree = Parser(unit.language).parse(source)
    query = {"defs": unit.defs, "refs": unit.refs, "imports": unit.imports}[which]
    return {
        node.text.decode("utf-8")
        for nodes in QueryCursor(query).captures(tree.root_node).values()
        for node in nodes
    }


@pytest.mark.parametrize(
    "filename,expected_defs",
    [
        ("app.ts", {"Greeter", "greet", "main"}),
        ("helpers.js", {"formatName"}),
        ("server.go", {"Server", "Start", "main"}),
    ],
)
def test_definition_queries_actually_capture(filename, expected_defs):
    spec = spec_for_extension(Path(filename).suffix)
    found = _captures(spec, (FIXTURES / filename).read_bytes(), "defs")
    assert expected_defs <= found, f"missing {expected_defs - found}"


@pytest.mark.parametrize(
    "filename,expected_refs",
    [("app.ts", {"formatName", "greet"}), ("server.go", {"Println", "Start"})],
)
def test_reference_queries_actually_capture(filename, expected_refs):
    spec = spec_for_extension(Path(filename).suffix)
    found = _captures(spec, (FIXTURES / filename).read_bytes(), "refs")
    assert expected_refs <= found, f"missing {expected_refs - found}"


def test_typescript_import_text_parses_to_a_module():
    spec = spec_for_extension(".ts")
    assert spec.parse_imports('import { formatName } from "./helpers.js";', "") == ["./helpers.js"]


def test_go_import_text_parses_to_a_module():
    spec = spec_for_extension(".go")
    assert spec.parse_imports('import "fmt"', "") == ["fmt"]
    assert spec.parse_imports('import (\n"fmt"\n"os"\n)', "") == ["fmt", "os"]


def test_python_behaviour_is_unchanged():
    spec = spec_for_extension(".py")
    assert spec.parse_imports("from pkg import api", "") == ["pkg", "pkg.api"]
```

- [x] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_languages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.core.tools.languages'`

- [x] **Step 4: Write minimal implementation**

```python
# pyrrhon/core/tools/languages.py
"""Which languages the symbol index understands, and how.

M4 shipped Python-only with one module-level grammar and three module-level
queries, and `_iter_files_with_mtime` hardcoded `.py`. That shape makes a new
grammar a rewrite rather than a table entry — and worse, a grammar added
without touching the walk silently indexes nothing, because no file with that
extension is ever yielded. M10's postscript flagged exactly this.

Grammars compile lazily and are cached: a Python-only repo must not pay to
load the Go grammar, and `get_language` plus three `Query` compilations is real
startup cost per language.

Import semantics differ per language and are not expressible as a tree-sitter
query, so each spec carries a small text parser for the import statements its
query captured. A false import edge is harmless; a missing one is not.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from tree_sitter import Language, Query
from tree_sitter_language_pack import get_language


@dataclass(frozen=True)
class LanguageSpec:
    name: str                      # tree-sitter-language-pack grammar name
    extensions: tuple[str, ...]
    def_query: str                 # captures named @def.<kind>
    ref_query: str                 # captures named @ref
    import_query: str              # captures whole import statements as @import
    parse_imports: Callable[[str, str], list[str]]  # (statement_text, package) -> modules


@dataclass(frozen=True)
class CompiledLanguage:
    language: Language
    defs: Query
    refs: Query
    imports: Query


@lru_cache(maxsize=None)
def compiled(spec: LanguageSpec) -> CompiledLanguage:
    """Grammar + queries for one spec, compiled once per process."""
    language = get_language(spec.name)
    return CompiledLanguage(
        language=language,
        defs=Query(language, spec.def_query),
        refs=Query(language, spec.ref_query),
        imports=Query(language, spec.import_query),
    )
```

Move these out of `ast_index.py` into this module, bodies unchanged (they are
correct and tested — this is a move, not a rewrite):

| From `ast_index.py` | To `languages.py` | Note |
|---|---|---|
| the `_DEF_QUERY` source string | `_PY_DEFS` | string only; the compiled `Query` now comes from `compiled()` |
| the `_REF_QUERY` source string | `_PY_REFS` | same |
| the `_IMPORT_QUERY` source string | `_PY_IMPORTS` | same |
| `_modules_from_import(stmt_text, package)` | `_parse_python_imports(stmt_text, package)` | **renamed** to match the `parse_imports` slot; body identical |
| `_resolve_relative` | `_resolve_relative` | private helper of the above; move with it |

`_module_name` and `_package_of` stay in `ast_index.py` — they are used by
`find_importers` and `_reparse`, not by the table. Update
`tests/test_import_graph.py`, which imports `_modules_from_import` by name.

Then add the three new specs. **Verify every node name below against your
installed grammar using the capture test in Step 2 before moving on** — these
are written against tree-sitter-language-pack 1.12 and node names do move
between versions:

```python
_TS_DEFS = """
(function_declaration name: (identifier) @def.function)
(class_declaration name: (type_identifier) @def.class)
(method_definition name: (property_identifier) @def.method)
"""
_JS_DEFS = """
(function_declaration name: (identifier) @def.function)
(class_declaration name: (identifier) @def.class)
(method_definition name: (property_identifier) @def.method)
"""
_JSTS_REFS = """
(call_expression function: (identifier) @ref)
(call_expression function: (member_expression property: (property_identifier) @ref))
"""
_JSTS_IMPORTS = """
(import_statement) @import
(call_expression function: (identifier) @import (#eq? @import "require"))
"""

_GO_DEFS = """
(function_declaration name: (identifier) @def.function)
(method_declaration name: (field_identifier) @def.method)
(type_declaration (type_spec name: (type_identifier) @def.type))
"""
_GO_REFS = """
(call_expression function: (identifier) @ref)
(call_expression function: (selector_expression field: (field_identifier) @ref))
"""
_GO_IMPORTS = "(import_declaration) @import"


def _parse_js_imports(text: str, _package: str) -> list[str]:
    """Module specifiers from an ES import or a require() call."""
    import re

    return re.findall(r"""["']([^"']+)["']""", text)


def _parse_go_imports(text: str, _package: str) -> list[str]:
    """Every quoted path in an import declaration, single or block form."""
    import re

    return re.findall(r'"([^"]+)"', text)


LANGUAGES: tuple[LanguageSpec, ...] = (
    LanguageSpec("python", (".py",), _PY_DEFS, _PY_REFS, _PY_IMPORTS, _parse_python_imports),
    LanguageSpec("typescript", (".ts",), _TS_DEFS, _JSTS_REFS, _JSTS_IMPORTS, _parse_js_imports),
    LanguageSpec("tsx", (".tsx",), _TS_DEFS, _JSTS_REFS, _JSTS_IMPORTS, _parse_js_imports),
    LanguageSpec("javascript", (".js", ".mjs", ".cjs", ".jsx"), _JS_DEFS, _JSTS_REFS, _JSTS_IMPORTS, _parse_js_imports),
    LanguageSpec("go", (".go",), _GO_DEFS, _GO_REFS, _GO_IMPORTS, _parse_go_imports),
)

_BY_EXTENSION = {ext: spec for spec in LANGUAGES for ext in spec.extensions}
INDEXABLE_EXTENSIONS = frozenset(_BY_EXTENSION)


def spec_for_extension(ext: str) -> LanguageSpec | None:
    return _BY_EXTENSION.get(ext.lower())
```

- [x] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_languages.py -v`
Expected: PASS. If a capture test fails, the node name is wrong for your
grammar version — inspect the real tree with:
`python -c "from tree_sitter import Parser; from tree_sitter_language_pack import get_language; print(Parser(get_language('go')).parse(open('tests/fixtures/polyglot_repo/server.go','rb').read()).root_node)"`
and correct the query. Do not skip the test.

- [x] **Step 6: Commit**

```bash
git add pyrrhon/core/tools/languages.py tests/test_languages.py tests/fixtures/polyglot_repo/ tests/conftest.py
git commit -m "feat(index): table-driven language specs for python, ts/tsx, js, and go"
```

---

### Task 2: Index every language in the table

**Files:**
- Modify: `pyrrhon/core/tools/ast_index.py:28-66,201-286`
- Test: `tests/test_multilang_index.py` (create)

**Interfaces:**
- Consumes: `INDEXABLE_EXTENSIONS`, `spec_for_extension`, `compiled` from Task 1.
- Produces: `symbols` and `files` tables gain a `lang TEXT` column; `SymbolIndex.find_symbol` returns `(file, line, kind)` unchanged; `SymbolIndex.languages() -> dict[str, int]` (file count per language, for the orientation brief).

- [x] **Step 1: Write the failing test**

```python
# tests/test_multilang_index.py
import shutil
from pathlib import Path

import pytest

from pyrrhon.core.tools.ast_index import SymbolIndex

FIXTURES = Path(__file__).parent / "fixtures" / "polyglot_repo"


@pytest.fixture
def polyglot(tmp_path):
    """Copied, never indexed in place: indexing writes .pyrrhon/cache.db and
    tests/conftest.py fences the checked-in fixture tree against exactly that."""
    shutil.copytree(FIXTURES, tmp_path / "repo")
    return tmp_path / "repo"


async def test_typescript_definitions_are_indexed(polyglot):
    index = SymbolIndex(polyglot)
    await index.ensure_fresh()
    assert await index.find_symbol("Greeter") == [("app.ts", 3, "class")]


async def test_javascript_definitions_are_indexed(polyglot):
    index = SymbolIndex(polyglot)
    await index.ensure_fresh()
    rows = await index.find_symbol("formatName")
    assert ("helpers.js", 2, "function") in rows


async def test_go_definitions_are_indexed(polyglot):
    index = SymbolIndex(polyglot)
    await index.ensure_fresh()
    assert await index.find_symbol("Server") == [("server.go", 5, "type")]


async def test_cross_file_references_are_found(polyglot):
    index = SymbolIndex(polyglot)
    await index.ensure_fresh()
    refs = await index.find_references("formatName")
    assert any(file == "app.ts" for file, _line in refs)


async def test_a_language_census_is_available(polyglot):
    index = SymbolIndex(polyglot)
    await index.ensure_fresh()
    census = await index.languages()
    assert census == {"typescript": 1, "javascript": 1, "go": 1}


async def test_python_indexing_is_unchanged(tmp_path):
    (tmp_path / "m.py").write_text("def greet():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    assert await index.find_symbol("greet") == [("m.py", 1, "function")]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_multilang_index.py -v`
Expected: FAIL — `assert [] == [("app.ts", 3, "class")]`; the walk yields only `.py`.

- [x] **Step 3: Write minimal implementation**

Replace the module-level grammar/query globals in `ast_index.py` with imports
from `languages.py`. Add `lang` to the schema:

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, lang TEXT);
CREATE TABLE IF NOT EXISTS symbols (name TEXT, kind TEXT, file TEXT, line INTEGER, lang TEXT);
CREATE TABLE IF NOT EXISTS refs (name TEXT, file TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS imports (file TEXT, module TEXT);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols (name);
CREATE INDEX IF NOT EXISTS idx_refs_name ON refs (name);
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports (module);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports (file);
"""
```

**Schema migration:** `CREATE TABLE IF NOT EXISTS` will not add a column to an
existing `cache.db`, so every user's cache would keep the old shape and every
query naming `lang` would fail. Bump a schema version and rebuild:

```python
_SCHEMA_VERSION = 2


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create or REBUILD the cache when its shape changed.

    The cache is a derived artifact — every row can be regenerated by
    reparsing — so a version bump drops and rebuilds rather than migrating.
    That is cheaper to write, impossible to get subtly wrong, and costs one
    cold index on the first run after an upgrade.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != _SCHEMA_VERSION:
        for table in ("files", "symbols", "refs", "imports"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()
    else:
        conn.executescript(_SCHEMA)
```

Generalise the walk:

```python
    def _iter_files_with_mtime(self):
        """Yield (path, mtime, spec) for every indexable file, pruning SKIP_DIRS.

        The extension set comes from the language table, not a literal: M10's
        postscript recorded that a hardcoded '.py' here means a newly added
        grammar never sees a file, which is a silent failure rather than an
        error.
        """
        stack = [str(self.root)]
        while stack:
            try:
                scan = os.scandir(stack.pop())
            except OSError:
                continue
            with scan:
                for entry in scan:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIP_DIRS:
                            stack.append(entry.path)
                        continue
                    spec = spec_for_extension(os.path.splitext(entry.name)[1])
                    if spec is not None and entry.is_file(follow_symlinks=False):
                        yield Path(entry.path), entry.stat().st_mtime, spec
```

Parse through the table. Note the parser cache: `Parser` objects are not
thread-safe, so build one per language **per call**, not per file:

```python
    def _sync_ensure_fresh(self) -> None:
        with self._db_lock:
            conn = self._db_conn()
            known: dict[str, float] = dict(conn.execute("SELECT path, mtime FROM files"))
            parsers: dict[str, Parser] = {}
            seen: set[str] = set()
            for path, mtime, spec in self._iter_files_with_mtime():
                rel = path.relative_to(self.root).as_posix()
                seen.add(rel)
                if known.get(rel) == mtime:
                    continue
                unit = compiled(spec)
                parser = parsers.get(spec.name)
                if parser is None:
                    parser = parsers[spec.name] = Parser(unit.language)
                self._reparse(conn, parser, unit, spec, path, rel, mtime)
                self._generation += 1
            for rel in set(known) - seen:
                self._forget(conn, rel)
                self._generation += 1
            conn.commit()
```

`_reparse` takes `unit` and `spec` and uses `unit.defs` / `unit.refs` /
`unit.imports` and `spec.parse_imports` in place of the module globals, and
writes `spec.name` into the `lang` column of `files` and `symbols`.

Add the census:

```python
    async def languages(self) -> dict[str, int]:
        return await asyncio.to_thread(self._sync_languages)

    def _sync_languages(self) -> dict[str, int]:
        with self._db_lock:
            rows = self._db_conn().execute(
                "SELECT lang, COUNT(*) FROM files GROUP BY lang ORDER BY COUNT(*) DESC"
            ).fetchall()
        return {lang: count for lang, count in rows if lang}
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_multilang_index.py tests/test_symbol_index.py tests/test_ast_tools.py tests/test_import_graph.py tests/test_repo_map.py -v`
Expected: PASS

- [x] **Step 5: Confirm the cold-index cost on a real polyglot repo**

Run the index over a checkout with TS and Go present and record the wall time.
If it exceeds a few seconds, note it — M4's docstring already names the
remedy (a `ProcessPoolExecutor` behind the same async interface) and it is a
follow-up, not this task.

- [x] **Step 6: Commit**

```bash
git add pyrrhon/core/tools/ast_index.py tests/test_multilang_index.py
git commit -m "feat(index): index every language in the table; version and rebuild the cache"
```

---

### Task 3: `symbol_context` — one round instead of three

**Files:**
- Create: `pyrrhon/core/tools/symbol_context.py`
- Modify: `pyrrhon/core/grounding/evidence.py`
- Test: `tests/test_symbol_context.py`

**Interfaces:**
- Consumes: `SymbolIndex.find_symbol`, `.find_references`, `.list_imports`, `.find_importers`; `ReadFileTool`-style line reading.
- Produces: `SymbolContextTool(index: SymbolIndex, root: Path)` with `name = "symbol_context"`, parameters `{name: str, context_lines: int = 20}`.

**Why:** M10 measured that answering "how does X work" costs three round trips
(`find_symbol` → `find_references` → `read_file`). At voice latency each round
trip is a full model turnaround, so this is the single biggest remaining
structural latency win.

- [x] **Step 1: Write the failing test**

```python
# tests/test_symbol_context.py
from pyrrhon.core.tools.ast_index import SymbolIndex
from pyrrhon.core.tools.symbol_context import SymbolContextTool

SOURCE = '''\
def helper():
    return 1


def greet(name):
    """Say hello."""
    return f"hello {name} {helper()}"
'''

CALLER = "from mod import greet\n\ngreet('world')\n"


async def test_one_call_returns_definition_references_and_source(tmp_path):
    (tmp_path / "mod.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "caller.py").write_text(CALLER, encoding="utf-8")
    tool = SymbolContextTool(SymbolIndex(tmp_path), tmp_path)

    result = await tool.run(name="greet")

    assert "mod.py:5" in result          # the definition
    assert "caller.py:3" in result       # a reference
    assert "def greet(name):" in result  # the source window
    assert "Say hello." in result


async def test_an_unknown_symbol_says_so_without_inventing(tmp_path):
    (tmp_path / "mod.py").write_text(SOURCE, encoding="utf-8")
    tool = SymbolContextTool(SymbolIndex(tmp_path), tmp_path)
    result = await tool.run(name="nonexistent_symbol")
    assert "No definition" in result
    assert ":" not in result.replace("No definition found for 'nonexistent_symbol'.", "")


async def test_every_line_it_shows_becomes_citable_evidence(tmp_path):
    """M13 rule: a tool that returns path:line must feed the ledger, or the
    gate will downgrade citations the model was legitimately shown."""
    from pyrrhon.core.grounding.evidence import EvidenceLedger

    (tmp_path / "mod.py").write_text(SOURCE, encoding="utf-8")
    tool = SymbolContextTool(SymbolIndex(tmp_path), tmp_path)
    result = await tool.run(name="greet")

    ledger = EvidenceLedger()
    ledger.record_tool_result("symbol_context", {"name": "greet"}, result)
    assert ledger.observed("mod.py", 5)
    assert ledger.observed("mod.py", 7)  # inside the shown window
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_symbol_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.core.tools.symbol_context'`

- [x] **Step 3: Write minimal implementation**

```python
# pyrrhon/core/tools/symbol_context.py
"""symbol_context: everything about one symbol, in a single tool round.

"How does X work?" used to cost three model round trips — find_symbol, then
find_references, then read_file — and at voice latency a round trip is a whole
model turnaround, not a function call. M10 measured this as the largest
remaining structural cost in the loop.

The output deliberately keeps the `path:line` shape every other tool uses: the
model cites from it, the grounding gate parses it, and the M13 evidence ledger
harvests it. Changing the shape here would silently break all three.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.tools.ast_index import SymbolIndex
from pyrrhon.core.tools.base import Tool

MAX_REFERENCES = 20
DEFAULT_CONTEXT_LINES = 20


class SymbolContextTool(Tool):
    name = "symbol_context"
    description = (
        "Everything about one symbol in a single call: where it is defined, the "
        "source around the definition, what calls it, and the file's import "
        "edges. Prefer this over separate definition/reference/dependency "
        "lookups — it is one round trip instead of three."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact symbol name, e.g. 'run_turn'"},
            "context_lines": {
                "type": "integer",
                "description": f"Source lines to show around the definition (default {DEFAULT_CONTEXT_LINES})",
            },
        },
        "required": ["name"],
    }

    def __init__(self, index: SymbolIndex, root: Path):
        self.index = index
        self.root = root

    async def run(self, name: str, context_lines: int = DEFAULT_CONTEXT_LINES) -> str:
        await self.index.ensure_fresh()
        definitions = await self.index.find_symbol(name)
        if not definitions:
            return f"No definition found for '{name}'."
        context_lines = max(0, min(int(context_lines), 100))

        file, line, kind = definitions[0]
        sections = [f"{file}:{line}: {kind} {name}"]
        if len(definitions) > 1:
            sections.append("also defined at:")
            sections += [f"  {f}:{n}: {k} {name}" for f, n, k in definitions[1:]]

        source = await asyncio.to_thread(self._window, file, line, context_lines)
        sections += ["", "source:", source]

        references = await self.index.find_references(name)
        sections += ["", f"called from ({len(references)} site(s)):"]
        sections += [f"  {f}:{n}" for f, n in references[:MAX_REFERENCES]] or ["  (none)"]
        if len(references) > MAX_REFERENCES:
            sections.append(f"  …and {len(references) - MAX_REFERENCES} more")

        imports = await self.index.list_imports(file)
        importers = await self.index.find_importers(file)
        sections += ["", f"{file} imports: " + (", ".join(imports) or "(none)")]
        sections.append(f"{file} imported by: " + (", ".join(importers) or "(none)"))
        return "\n".join(sections)

    def _window(self, rel: str, line: int, context_lines: int) -> str:
        """Numbered source around the definition — same gutter format as
        read_file, which is what the evidence ledger parses."""
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            return "(source unavailable)"
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "(source unavailable)"
        first = max(1, line - 2)
        last = min(len(lines), line + context_lines)
        return "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1))
```

In `pyrrhon/core/grounding/evidence.py`, the numbered-gutter branch keys off
`args["path"]`, which `symbol_context` does not have. Add an explicit case:

```python
# symbol_context prints a numbered window of the DEFINING file, whose path is
# not in the arguments — it is the first path:line in the output. Handled
# separately so the window's lines count as observed, not just the header line.
if name == "symbol_context":
    references = extract_references(result)
    if references:
        defining = references[0][0]
        numbered = [int(n) for n in _NUMBERED.findall(result)]
        if numbered:
            self.record_range(defining, min(numbered), max(numbered))
    for rel, line in references:
        self.record_line(rel, line)
    return
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_symbol_context.py tests/test_evidence.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/symbol_context.py pyrrhon/core/grounding/evidence.py tests/test_symbol_context.py
git commit -m "feat(tools): symbol_context answers definition, references, and source in one round"
```

---

### Task 4: Slim the belt — a reviewed change to the safety fence

> **Amended 2026-08-15, before execution.** The original Task 4 rested on a
> superset claim that does not hold, and justified itself with a schema saving
> that measurement shows to be noise. Both are corrected below; the amendment
> record and its evidence are in **Appendix A**. Execute the steps as written
> here — the pre-amendment text is superseded, not optional.

**Files:**
- Modify: `pyrrhon/repl.py:143-192` (the `tools` list at 143, `deep_tools` at 181)
- Modify: `pyrrhon/core/tools/symbol_context.py` (from Task 3 — truncation rollup)
- Modify: `tests/test_safety.py:20-27`
- Test: `tests/test_safety.py`, `tests/test_symbol_context.py`

**Interfaces:**
- Consumes: `SymbolContextTool` from Task 3.
- Produces: the belt loses `find_references`, gains `symbol_context`. Belt size
  is unchanged at 15. `find_symbol` **and** `list_dependencies` both stay.

**This is the design discussion `tests/test_safety.py:1-9` demands, not a test
edit of convenience.** Recording the reasoning so the reviewer can judge it:

- **`find_references` goes.** It is name-addressed (`{"name": str}`) and
  `symbol_context` is name-addressed with the identical argument, returning the
  same `path:line` rows from the same read-only index plus more. Same address
  space, superset output — removing it narrows no capability and widens no
  permission. One caveat, fixed in Step 3: `symbol_context` truncates its
  reference list at `MAX_REFERENCES = 20`, so on a hot symbol it would answer
  *less* than `find_references` did. A truncation that keeps the full count and
  a per-file rollup closes that gap in one line of output.
- **`list_dependencies` stays.** It is *path*-addressed
  (`{"path": "pyrrhon/core/agent/loop.py"}`); `symbol_context` is
  *name*-addressed and returns import edges only for the file that happens to
  define the symbol you named. "What imports `loop.py`?" and "what does
  `settings.py` rely on?" carry no symbol to hang the query on, and both are
  first-class Understand-act questions. Different address spaces mean this was
  never a superset, and dropping it would have deleted a capability while the
  plan asserted it had not — exactly the kind of unreviewed narrowing the
  safety-fence docstring exists to catch.
- **`find_symbol` stays**, as originally reasoned: folding it in would force a
  source-window read on questions that only wanted a location, a latency
  regression disguised as simplification.

**What this task is actually worth.** The win is *round trips* — three model
turnarounds collapse to one on "how does X work", which at voice latency is the
largest remaining structural cost (Task 3). It is **not** a schema-size win.
Measured against the installed belt:

| Belt | schema_chars delta/turn |
|---|---|
| Task 4 as originally written (drop both, add `symbol_context`) | −188 |
| Task 4 as amended (drop `find_references` only) | **+219** |

188 chars is ~47 tokens against a ~1.5k-token belt: noise either way. The
amended belt costs ~55 tokens more per tool-bearing turn and buys back a
capability. Claim the round trips; do not claim the schema.

- [x] **Step 1: Write the failing test**

```python
# tests/test_safety.py — replace the EXPECTED_BELT constant
EXPECTED_BELT = {
    "read_file", "grep", "glob", "remember",
    "find_symbol", "symbol_context", "list_dependencies", "repo_map",
    "git_log", "git_blame", "git_show",
    "web_search", "web_fetch", "write_spec", "think_deeper",
}

READ_ONLY = EXPECTED_BELT - {"write_spec", "remember", "think_deeper"}
```

```python
# tests/test_safety.py (append)
def test_symbol_context_replaced_find_references_and_added_no_capability(agent):
    """The M14 belt change. symbol_context takes the same `name` argument as
    find_references and returns its rows plus more, from the same read-only
    index, so nothing new became reachable and nothing became unanswerable."""
    assert "symbol_context" in agent.tools
    assert "find_references" not in agent.tools


def test_path_addressed_dependency_questions_survived_the_belt_change(agent):
    """list_dependencies is path-addressed; symbol_context is name-addressed.
    'What imports loop.py?' has no symbol to hang on, so this tool is not
    redundant and must not be dropped as if it were."""
    assert "list_dependencies" in agent.tools
    assert "path" in agent.tools["list_dependencies"].parameters["properties"]


def test_the_deep_subagent_belt_gained_symbol_context_and_stayed_read_only(agent):
    deep = agent.tools["think_deeper"]
    assert "symbol_context" in deep.tools
    assert set(deep.tools) <= READ_ONLY
```

```python
# tests/test_symbol_context.py (append)
async def test_truncated_references_still_report_the_full_blast_radius(tmp_path):
    """Dropping find_references is only safe if truncation stays lossless in
    aggregate: the full count and the per-file spread must survive the cap."""
    (tmp_path / "mod.py").write_text("def hot():\n    return 1\n", encoding="utf-8")
    for i in range(3):
        (tmp_path / f"c{i}.py").write_text(
            "from mod import hot\n" + "hot()\n" * 10, encoding="utf-8"
        )
    tool = SymbolContextTool(SymbolIndex(tmp_path), tmp_path)

    result = await tool.run(name="hot")

    assert "30 site(s)" in result           # full count, not the shown count
    assert "…and 10 more" in result         # the cap is declared, not silent
    assert result.count("c0.py") >= 1       # every calling file still named
    assert "c1.py" in result and "c2.py" in result
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_safety.py tests/test_symbol_context.py -v`
Expected: FAIL — `symbol_context` is not in the belt; the rollup assertion fails
because Task 3's truncation drops the tail silently.

- [x] **Step 3: Write the implementation**

First, make the truncation lossless in aggregate. In
`pyrrhon/core/tools/symbol_context.py`, replace the reference block from Task 3:

```python
        references = await self.index.find_references(name)
        sections += ["", f"called from ({len(references)} site(s)):"]
        sections += [f"  {f}:{n}" for f, n in references[:MAX_REFERENCES]] or ["  (none)"]
        if len(references) > MAX_REFERENCES:
            # Blast radius has to survive the cap. Listing 200 call sites would
            # blow the context budget, but "…and 180 more in a.py (140), b.py
            # (40)" is the part "what breaks if I change this?" actually needs,
            # and it costs one line. Without it, dropping find_references from
            # the belt would silently cap that answer at 20 — which is what
            # made the original superset claim false in output as well as in
            # addressing.
            spread = Counter(f for f, _ in references[MAX_REFERENCES:])
            listed = ", ".join(f"{f} ({n})" for f, n in spread.most_common())
            sections.append(f"  …and {len(references) - MAX_REFERENCES} more in {listed}")
```

(add `from collections import Counter` to the module imports)

Then, in `pyrrhon/repl.py`'s `build_agent`, replace `FindReferencesTool(index)`
with `SymbolContextTool(index, repo_root)` in **both** the main belt (line 143)
and `deep_tools` (line 181). **Leave `DependenciesTool(index)` in both** — see
the reasoning above. Leave the `FindReferencesTool` class in place; it is still
useful programmatically and its own tests still pass.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_safety.py tests/test_symbol_context.py tests/test_build_agent_m4.py -v`
Expected: PASS

- [x] **Step 5: Pin the belt's schema budget deterministically**

The original step said to read `trace.schema_chars` "via `/debug-history` or the
latency harness `--json`" and compare against "the M13 baseline". Neither exists:
`/debug-history` dumps history messages and never touches the trace
(`pyrrhon/commands/debug_cmd.py:1-35`), and no milestone ever recorded a
`schema_chars` baseline. `schema_chars` is also a *static* property of the belt
(`pyrrhon/core/agent/loop.py:271`) — it needs no live turn at all, so pin it in a
test where a regression is caught by CI rather than by a developer remembering
to look:

```python
# tests/test_safety.py (append)
# The belt's schema rides on every tool-bearing turn, so its size is a latency
# property, not a style one. Pinned as a ceiling rather than an equality: a
# tool description may be reworded, but the belt may not quietly double.
MAX_BELT_SCHEMA_CHARS = 7000


def test_the_belt_schema_stays_within_its_latency_budget(agent):
    total = sum(len(str(s)) for s in agent._tool_schemas())
    assert total <= MAX_BELT_SCHEMA_CHARS, (
        f"belt schema grew to {total} chars; every tool-bearing turn pays this"
    )
```

Run it, then set `MAX_BELT_SCHEMA_CHARS` to the measured value rounded up to the
next 500, and record the exact measured number in the commit body. Claim the
round trips in the message, not a schema saving — the amended belt is ~219 chars
*larger* per turn and that is the right trade.

- [x] **Step 6: Commit**

```bash
git add pyrrhon/repl.py pyrrhon/core/tools/symbol_context.py tests/test_safety.py tests/test_symbol_context.py
git commit -m "refactor(belt): symbol_context replaces find_references, one round instead of three"
```

---

### Task 5: A repo map that knows what the conversation is about

**Files:**
- Modify: `pyrrhon/core/tools/ast_index.py:322-369`
- Modify: `pyrrhon/core/tools/ast_index.py` (`RepoMapTool`)
- Modify: `pyrrhon/core/agent/loop.py`
- Test: `tests/test_repo_map.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `SymbolIndex.build_repo_map(max_chars: int = 6000, mentioned: frozenset[str] = frozenset())`; `RepoMapTool(index, mentions: Callable[[], frozenset[str]] | None = None)`.

**Why:** M10 Stage 3's description — the map is "plain cross-file reference
counting with no conversation awareness", so asking about auth returns the same
ranking as asking about the parser.

- [x] **Step 1: Write the failing test**

```python
# tests/test_repo_map.py (append)
async def test_mentioned_files_are_boosted_to_the_top(tmp_path):
    # `hot.py` is referenced more; `quiet.py` is what the user is asking about.
    (tmp_path / "hot.py").write_text("def popular():\n    pass\n", encoding="utf-8")
    (tmp_path / "quiet.py").write_text("def obscure():\n    pass\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from hot import popular\n\npopular()\npopular()\npopular()\n", encoding="utf-8"
    )
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()

    plain = await index.build_repo_map()
    boosted = await index.build_repo_map(mentioned=frozenset({"quiet.py"}))

    assert plain.index("hot.py") < plain.index("quiet.py")
    assert boosted.index("quiet.py") < boosted.index("hot.py")


async def test_the_boost_does_not_invent_files(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    rendered = await index.build_repo_map(mentioned=frozenset({"ghost.py"}))
    assert "ghost.py" not in rendered


async def test_the_cache_distinguishes_different_mention_sets(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    first = await index.build_repo_map(mentioned=frozenset({"a.py"}))
    second = await index.build_repo_map(mentioned=frozenset({"b.py"}))
    assert first != second  # a generation-only cache key would return `first`
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repo_map.py -v`
Expected: FAIL — `TypeError: build_repo_map() got an unexpected keyword argument 'mentioned'`

- [x] **Step 3: Write minimal implementation**

```python
# A mentioned file outranks reference count, but does not erase it: within the
# mentioned set the usual ranking still applies. Additive rather than a
# separate section so the map stays one ordered list the model can read top-down.
MENTION_BOOST = 1000


    def _sync_build_repo_map(self, max_chars: int, mentioned: frozenset[str]) -> str:
        # The cache key now includes the mention set: keying on generation
        # alone would serve the previous question's ranking for the rest of
        # the session, which is worse than no personalisation at all.
        cached = self._repo_map_cache
        key = (self._generation, max_chars, mentioned)
        if cached is not None and cached[0] == key:
            return cached[1]
        ...
        ranked = sorted(
            by_file.items(),
            key=lambda item: (
                sum(u for *_ignored, u in item[1])
                + (MENTION_BOOST if item[0] in mentioned else 0)
            ),
            reverse=True,
        )
        ...
        self._repo_map_cache = (key, rendered)
        return rendered
```

In `pyrrhon/core/agent/loop.py`, collect mentions from the live turn. Paths the
user or the model named are exactly the files worth ranking up:

```python
    def _conversation_mentions(self, history: list[dict]) -> frozenset[str]:
        """Repo paths named anywhere in the recent conversation.

        Reuses the citation regex rather than a new one: a path worth citing is
        a path worth ranking up, and one regex is one thing to keep correct.
        Bounded to the last 12 messages so a long session does not make every
        file 'mentioned'.
        """
        recent = history[-12:]
        found: set[str] = set()
        for message in recent:
            content = message.get("content")
            if isinstance(content, str):
                found.update(rel for rel, _line in extract_references(content))
        return frozenset(found)
```

`RepoMapTool.run` takes the mention set from a callable the agent supplies at
build time, so the tool stays free of a back-reference to the agent:

```python
class RepoMapTool(Tool):
    def __init__(self, index: SymbolIndex, mentions: Callable[[], frozenset[str]] | None = None):
        self.index = index
        self._mentions = mentions or (lambda: frozenset())

    async def run(self) -> str:
        await self.index.ensure_fresh()
        return await self.index.build_repo_map(mentioned=self._mentions())
```

`Agent` sets `self._mentions_now` at the top of each turn from
`_conversation_mentions(history)`, and `build_agent` wires
`RepoMapTool(index, mentions=lambda: agent._mentions_now)` — which requires
constructing the agent before the tool, so build the belt, construct the
`Agent`, then patch the tool's `_mentions` callable. Do it explicitly with a
comment rather than a closure over a not-yet-bound name.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repo_map.py tests/test_ast_tools.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/ast_index.py pyrrhon/core/agent/loop.py pyrrhon/repl.py tests/test_repo_map.py
git commit -m "feat(repo-map): rank files the conversation is actually about"
```

---

### Task 6: The orientation brief

**Files:**
- Create: `pyrrhon/core/tools/orientation.py`
- Modify: `pyrrhon/repl.py`, `pyrrhon/tui/app.py`
- Test: `tests/test_orientation.py`

**Interfaces:**
- Consumes: `SymbolIndex.languages()` (Task 2), `SymbolIndex.build_repo_map`, `GitLogTool`.
- Produces: `build_orientation(repo_root: Path, index: SymbolIndex) -> ScreenArtifact` — the first real emitter of `ScreenArtifact`, unused since M0.

- [x] **Step 1: Write the failing test**

```python
# tests/test_orientation.py
from pyrrhon.core.events import ScreenArtifact
from pyrrhon.core.tools.ast_index import SymbolIndex
from pyrrhon.core.tools.orientation import build_orientation


async def test_the_brief_is_a_screen_artifact_not_speech(tmp_path):
    """Screen-only by construction: it is a dense list of paths and counts,
    which is precisely what VOICE_STYLE forbids reading aloud."""
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    brief = await build_orientation(tmp_path, index)
    assert isinstance(brief, ScreenArtifact)
    assert brief.kind == "markdown"


async def test_the_brief_names_the_languages_and_the_busiest_files(tmp_path):
    (tmp_path / "core.py").write_text("def shared():\n    pass\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from core import shared\n\nshared()\nshared()\n", encoding="utf-8"
    )
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    content = (await build_orientation(tmp_path, index)).content
    assert "python" in content.lower()
    assert "core.py" in content


async def test_an_empty_repo_produces_an_honest_brief(tmp_path):
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    content = (await build_orientation(tmp_path, index)).content
    assert "no indexed source" in content.lower()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orientation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.core.tools.orientation'`

- [x] **Step 3: Write minimal implementation**

```python
# pyrrhon/core/tools/orientation.py
"""The first thing worth knowing about a repo you have never opened.

Act 1's premise is a codebase you didn't write, and the session currently
starts with a blank prompt — the user has to know enough to ask a first
question, which is exactly what they don't have yet.

Emitted as a ScreenArtifact, deliberately: it is a dense census of languages,
files and counts, and VOICE_STYLE forbids reading tables and path lists aloud.
The voice channel says one sentence about it; the screen carries the detail.
This is the event type's first real use since M0 defined it.
"""

from __future__ import annotations

from pathlib import Path

from pyrrhon.core.events import ScreenArtifact
from pyrrhon.core.tools.ast_index import SymbolIndex
from pyrrhon.core.tools.git import GitLogTool

MAP_CHARS = 1500


async def build_orientation(repo_root: Path, index: SymbolIndex) -> ScreenArtifact:
    await index.ensure_fresh()
    census = await index.languages()
    if not census:
        return ScreenArtifact(
            kind="markdown",
            content=(
                f"## {repo_root.name}\n\n"
                "No indexed source found — no files in a language Pyrrhon "
                "indexes yet (python, typescript, javascript, go). Ask about "
                "any file directly and it will read it."
            ),
        )
    languages = ", ".join(f"{lang} ({count})" for lang, count in census.items())
    repo_map = await index.build_repo_map(max_chars=MAP_CHARS)
    recent = await GitLogTool(repo_root).run(max_count=5)
    return ScreenArtifact(
        kind="markdown",
        content=(
            f"## {repo_root.name}\n\n"
            f"**Languages:** {languages}\n\n"
            f"**Most-referenced code**\n\n```\n{repo_map}\n```\n\n"
            f"**Recent commits**\n\n```\n{recent}\n```"
        ),
    )
```

Both channels render it at startup, after the banner. In `repl.py`'s
`_repl_main` and `app.py`'s `on_mount`, run it in the background beside the
existing warm-ups — it must never delay the first prompt:

```python
        async def _orient() -> None:
            try:
                self._render_event(await build_orientation(self.repo_root, index))
            except Exception:  # a brief is a nicety; never let it break startup
                log.debug("orientation brief failed", exc_info=True)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orientation.py tests/test_tui_app.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/orientation.py pyrrhon/repl.py pyrrhon/tui/app.py tests/test_orientation.py
git commit -m "feat(orientation): screen brief on an unfamiliar repo at session start"
```

---

### Task 7: `evals/understanding.yaml` — the quality claim, measured

**Files:**
- Create: `evals/understanding.yaml`
- Modify: `pyrrhon/evals/grounding.py`
- Test: `tests/test_grounding_eval.py` (append)

**Interfaces:**
- Consumes: `TurnTrace.tool_calls`, `TurnTrace.rounds` (already recorded).
- Produces: a `max_rounds` case key — the case fails if the turn took more model rounds than allowed.

**Why:** M10's postscript says `evals/understanding.yaml` "exists to prove
Stage 3's quality claim and measures nothing without it." The claim is
specifically that a dependency question costs one tool round instead of three,
so the eval has to assert the round count, not just the citation.

- [x] **Step 1: Write the failing test**

```python
# tests/test_grounding_eval.py (append)
from pyrrhon.evals.grounding import _check_rounds


def test_a_case_can_cap_the_number_of_model_rounds():
    assert _check_rounds({"max_rounds": 2}, rounds=2) is None
    problem = _check_rounds({"max_rounds": 2}, rounds=4)
    assert problem is not None and "4" in problem


def test_a_case_without_a_cap_never_fails_on_rounds():
    assert _check_rounds({}, rounds=99) is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounding_eval.py -k rounds -v`
Expected: FAIL — `ImportError: cannot import name '_check_rounds'`

- [x] **Step 3: Write minimal implementation**

```python
def _check_rounds(case: dict, rounds: int) -> str | None:
    """None when within budget, else an explanation.

    Round count is the measurable form of the code-intelligence claim: folding
    three lookups into symbol_context is only real if the model stops taking
    three turns to answer.
    """
    cap = case.get("max_rounds")
    if cap is None or rounds <= cap:
        return None
    return f"took {rounds} model rounds, expected at most {cap}"
```

Call it in `_run_cases` alongside `_check`, using the trace already collected:

```python
            problems = [
                p for p in (
                    _check(citations, case),
                    _check_rounds(case, len(trace.rounds) if trace else 0),
                ) if p
            ]
```

```yaml
# evals/understanding.yaml
# Code-intelligence eval (M14). Run against Pyrrhon itself:
#   uv run python -m pyrrhon.evals.grounding evals/understanding.yaml --repo .
#
# `max_rounds` is the point of this file: symbol_context claims to answer a
# dependency question in ONE tool round where find_symbol -> find_references ->
# read_file took three. A citation-only eval cannot tell those apart.

- question: "Where is run_turn defined and what calls it?"
  max_rounds: 2
  expected:
    - {file: pyrrhon/core/agent/loop.py, line: 221}

- question: "What depends on the grounding gate?"
  max_rounds: 2
  expected_any:
    - {file: pyrrhon/core/agent/loop.py, line: 49}
    - {file: pyrrhon/repl.py, line: 19}

- question: "Where is the tool guard's duplicate check, and who calls it?"
  max_rounds: 2
  expected:
    - {file: pyrrhon/core/agent/guards.py, line: 41}

- question: "What is the busiest file in this repo and why?"
  max_rounds: 2
  expected_any:
    - {file: pyrrhon/core/agent/loop.py, line: 175}
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounding_eval.py -v`
Expected: PASS

- [x] **Step 5: Run the eval and record the numbers**

Run: `uv run python -m pyrrhon.evals.grounding evals/understanding.yaml --repo . --repeat 3 --json understanding.json`
Expected: a score plus the round counts. If `max_rounds` fails, that is the
finding — `symbol_context` is not being reached for. Fix the tool description
before relaxing the cap, and record what changed.

- [x] **Step 6: Commit**

```bash
git add evals/understanding.yaml pyrrhon/evals/grounding.py tests/test_grounding_eval.py
git commit -m "test(evals): measure the code-intelligence claim in model rounds, not just citations"
```

---

## Implementation record

Executed 2026-08-15 on branch `m14-code-intelligence`, against
tree-sitter 0.25.2 / tree-sitter-language-pack 1.12.2.

### Where the plan was wrong

**Fixture line numbers were internally inconsistent (Task 1 Step 1 / Task 2).**
The fixture listings carry a `// tests/fixtures/...` header comment, but Task 2
then expects `Greeter` at `app.ts:3` and `Server` at `server.go:5` — true only
*without* the header — while expecting `formatName` at `helpers.js:2`, true only
*with* it. Resolved by dropping the header from all three fixtures and
correcting the expectation to `helpers.js:1`.

**The `require()` import query was a no-op (Task 1 Step 4).** As specified,
`(call_expression function: (identifier) @import (#eq? @import "require"))`
captures the *identifier* `require`, whose text contains no module specifier, so
`_parse_js_imports` returns `[]` — a silently dead import edge for the form most
`.js` in the wild actually uses. Corrected to capture the whole call:

```
((call_expression function: (identifier) @_fn) @import
 (#eq? @_fn "require"))
```

Verified that the `#eq?` predicate genuinely filters (py-tree-sitter applies
standard predicates in `QueryCursor.captures`) — without that, every call
expression would have become an import edge. Pinned by
`test_a_commonjs_require_is_an_import_edge_too`.

**Every other node name in the plan was correct** for language-pack 1.12.2. No
query needed adjusting; all capture tests passed first run.

**Task 4's truncation test passed for the wrong reason.** `assert "c2.py" in
result` is satisfied by the `imported by:` line, which says nothing about call
sites, so the test was green before the rollup existed. Tightened to assert the
rollup text itself (`"…and 10 more in c2.py (10)"`), which is genuinely RED
against Task 3's truncation.

**Task 4 missed the voice filler.** `TOOL_FILLERS` in `pyrrhon/voice/bridge.py`
is keyed on tool name; dropping `find_references` from the belt without adding
`symbol_context` would leave the voice channel silent during the commonest
lookup. Added.

**The Verification section's grounding command is wrong.** It says
`grounding.yaml --repo .`, but per `evals/README.md` and the file's own header
`grounding.yaml` runs against `tests/fixtures/sample_repo` with **no** `--repo`,
and only `grounding-self.yaml` takes `--repo .`. Run as written, every expected
path is missing. Note also that running the fixture set leaves
`tests/fixtures/sample_repo/.pyrrhon/cache.db` behind, which trips
`conftest.py`'s pristine-fixture fence on the *next* pytest run — delete it
afterwards.

**The eval CLI could not see stored credentials (Task 7 Step 5).**
`pyrrhon --setup` writes keys to `~/.pyrrhon/credentials.toml`, and only
`config/wizard.py` ever read them back, so the eval command CLAUDE.md documents
died with `MissingAPIKeyError` on a correctly configured machine. Added
`load_credentials()` to both `evals/grounding.py` and `evals/design.py` mains;
`setdefault` semantics keep a real env var winning.

### Measured

**Cold index** (Task 2 Step 5):

| Repo | Files | Cold | Warm re-walk |
|---|---|---|---|
| Pyrrhon (python-dominant) | 154 | 438 ms | 9 ms |
| A real TS/JS checkout | 38 | 1070 ms | — |

Grammar first-compile: typescript 46.5 ms, javascript 22.9 ms, python 21.0 ms,
go 7.3 ms (~100 ms for all four). Lazy compilation therefore saves a
python-only repo ~77 ms. Both well inside "a few seconds" — the
`ProcessPoolExecutor` follow-up M4's docstring names is not needed yet.

**Belt schema** (Task 4 Step 5): **6892 chars over 15 tools**, ceiling pinned at
7000. Per-tool: grep 788, write_spec 673, think_deeper 597, symbol_context 595,
git_blame 493, git_log 458, read_file 454, web_search 445, list_dependencies
407, remember 369, find_symbol 343, web_fetch 343, git_show 337, repo_map 313,
glob 277. This confirms Appendix A exactly: `symbol_context` 595 vs
`find_references` 376 = **+219 chars/turn**. The milestone claims the round-trip
collapse, not a schema saving.

Note the ceiling leaves only 108 chars of headroom, which sits awkwardly against
the test comment's "a tool description may be reworded". Kept at the plan's
"round up to the next 500" rule; revisit if it trips on an innocuous edit.

### Task 7 Step 5: the eval found what the plan predicted it might

First run, 2/5 passed, with two cases blowing `max_rounds` at 7 and 8. The plan
says to treat that as the finding and fix the tool description before relaxing
the cap. Tracing one case showed the cause exactly:

```
Q: Where is the tool guard's duplicate check, and who calls it?
   tools=[grep, read_file]   rounds=3      # symbol_context never called
```

The original description only claimed to beat "separate definition/reference/
dependency lookups", and the model does not classify `grep` as one of those.
Rewritten to gate on knowing an exact identifier, to name `grep` explicitly, and
to keep a fall-back clause so concepts still route to search. After:

```
Q: Where is run_turn defined and what calls it?
   tools=[symbol_context]    rounds=2      # the floor: 1 tool round + 1 answer
Q: Where is the tool guard's duplicate check, and who calls it?
   tools=[grep, symbol_context, ...]       # correct fall-back; phrase, not a name
```

That second result exposed a flaw in the eval itself, not the tool: the question
names a *concept*, so no amount of steering can make it a 2-round question — a
search has to turn the phrase into a name first. `understanding.yaml` now
separates IDENTIFIER cases (`max_rounds: 2`, a real assertion about the claim)
from CONCEPT cases (a discovery round is legitimate). Capping the latter at 2
was measuring the wrong thing.

Belt schema after the rewrite: **7087 chars**, `symbol_context` 595 → 790.
Ceiling raised 7000 → 7500. ~49 extra tokens per tool-bearing turn to remove up
to two model round trips.

**Recorded result** (`--repo .`, cerebras/gemma-4-31b, 2026-08-15):

| | before description fix | after |
|---|---|---|
| passed | 2/5 | **4/6** |
| total_ms median | 63,410 | **2,898** |
| llm_ms median | 63,298 | 2,799 |
| tool_wall_ms median | 111 | 65 |
| gate_ms median | 11 | 7 |
| provenance downgrades | 0 | 0 |

The 22x latency drop is the first run having been rate-limited, not a code win —
single questions measured 1-2s per LLM round throughout. Do not read either
column as a channel latency baseline.

**Two cases still fail, left as findings rather than tuned green:**

1. *"Where is GroundingGate defined and who uses it?"* — 3 rounds against a cap
   of 2. One round over; the identifier is named, so this is the claim not quite
   landing rather than a mis-specified case. Worth a trace before M15.
2. *"What is the busiest file in this repo and why?"* — cited
   `pyrrhon/core/providers/llm.py:80` where the repo map ranks
   `ast_index.py` first. "Busiest" is a judgment call, so a citation assertion
   is a weak instrument here; either the case needs a sharper question or the
   model is not consulting `repo_map`. Not resolved.

Widening `expected_any` until both pass would be teaching to the test, so
neither was touched.

### Manual polyglot check (Verification item 3)

Pointed at a real TypeScript checkout (28 indexed files: 22 ts, 6 js) and asked
"Where is SurfaceStore defined and what calls it?", against ground truth
confirmed by hand first:

```
tools : ['symbol_context', 'grep']       # symbol_context reached for FIRST
rounds: 3
cites : src/render/store.ts:84  <- hand-verified `export class SurfaceStore {`
        src/main.ts:32          <- hand-verified instantiation site
```

The answer was prose plus `path:line`, no fenced code — the TEXT_STYLE change
holding outside the fixture too.

**A real Go repo was not available on this machine**, so Go coverage rests on
`tests/fixtures/polyglot_repo/server.go` (capture tests, indexing tests, and
`languages()` census) rather than a live checkout. Worth doing before M15.

### Answers pointed at, not pasted (2026-08-15, user-directed)

`TEXT_STYLE` used to say "short fenced code snippets are welcome", which made
answers reprint source the reader already has. Changed to: say what the code
does in prose, cite `path:line`, quote at most a short inline expression when
the exact wording is the point.

To make that trade honest the pointer has to be followable, so citations are now
OSC 8 hyperlinks (`pyrrhon/core/citation_link.py`) in both channels — Rich
`[link=]` in the REPL, a `link` style in the TUI, alongside the existing code
viewer. `file://…#L<n>`; terminals without OSC 8 render the text unchanged.
The URI is re-checked for repo containment because this is the step that hands a
model-produced path to the user's shell.

**The source window in `symbol_context` output was NOT removed**, and the
measurement is why: the window is 1124 chars (~281 tokens, 23% of the output)
and the whole tool call costs 2.8ms. Dropping it would force a `read_file`
round back on — a full model turnaround, 1-2s — to save ~281 tokens of prefill.
It is also never shown to the user: channels print `→ symbol_context({args})`,
never the result. The visible code came from `TEXT_STYLE`, which is what
changed.

### Unrelated defect found and fixed

`tests/test_telemetry.py::test_tool_round_records_each_call_and_the_round_wall_clock`
began failing ~5 of 8 full-suite runs on this branch while passing 5/5 on `dev`
and 5/5 in isolation. Investigated rather than dismissed as flake.

Root cause is a **test defect**, not a dispatch regression. The tools are bare
`asyncio.sleep(0.05)` and `_run_tool` is a bare `await`, so nothing inside the
measured span can consume time. Instrumenting the suite showed gen2 GC pauses of
90–194 ms are routine by the time these tests run (~290k live objects). A pause
inside the ~50 ms window inflates the slowest span without changing the sum,
dragging `parallel_speedup` toward 1.0 — the exact value that signals sequential
dispatch, so a false negative and a true positive are indistinguishable. The
observed failure was 1.4986 against a 1.5 threshold.

The branches differ by 1% in heap (dev 290,708 vs M14 293,730 live objects) but
sit at different GC phases (`counts=(283,5,5)` vs `(542,4,8)`): M14's 42 extra
tests merely re-roll when the collector fires.

Fixed by suspending automatic collection across the measurement, which removes
the confound rather than relaxing the threshold — sequential dispatch still
scores 1.0 and still fails. Verified 5/5 clean full runs.

## Verification

Before opening the PR:

- [ ] `uv run pytest -q` — all green
- [ ] `uv run ruff check . && uv run mypy pyrrhon/core` — clean
- [ ] Manual: point Pyrrhon at a real TypeScript repo and at a real Go repo; ask
      "where is X defined and what calls it" for a symbol you have verified by hand
- [ ] `uv run python -m pyrrhon.evals.grounding evals/understanding.yaml --repo .` — recorded
- [ ] `uv run python -m pyrrhon.evals.grounding evals/grounding.yaml --repo .` — no regression from M13
- [ ] Latency `--compare` against the M13 baseline: round counts should be
      lower on "how does X work" questions, so `first_speech_ms` should IMPROVE
      — from the round trips, not from belt size, which grows slightly (see
      Appendix A). If it did not improve, say so in the record rather than
      quietly shipping.

---

## Appendix A — Task 4 amendment record (2026-08-15)

Written before execution, after checking Task 4's claims against the code it
proposes to change. Three findings, all in Task 4; Tasks 1–3 and 5–7 stand.

**A1. The superset claim was false for `list_dependencies`.** Task 4 justified
removing two tools by asserting `symbol_context` "returns a strict superset" of
both. It does for `find_references` — same `{"name": str}` argument, same rows,
plus more. It does not for `list_dependencies`, whose parameter is
`{"path": str}` (`pyrrhon/core/tools/ast_index.py:429-435`) while
`symbol_context` accepts only a symbol name and reports import edges solely for
that symbol's defining file (Task 3, `symbol_context.py` `run()`). "What imports
`pyrrhon/core/agent/loop.py`?" has no symbol to pass. Removing it would have
deleted a capability under a written claim that nothing was narrowed — landing a
`tests/test_safety.py` fence edit on a false premise, which is precisely what
that file's docstring forbids. **Resolution:** `list_dependencies` stays; the
belt goes 15 → 15, not 15 → 14.

**A2. The truncation made even the `find_references` superset false in output.**
Task 3 caps the reference list at `MAX_REFERENCES = 20`. On a hot symbol,
`find_references` returned all 200 call sites and `symbol_context` would return
20 — so retiring it would silently cap "what breaks if I change this?", the
question the tool exists for. **Resolution:** Task 4 Step 3 now amends Task 3's
truncation to keep the full count and add a per-file rollup of the tail. The cap
stays (the context budget is real); the blast radius survives it at file
granularity for one line of output.

**A3. The performance justification was unmeasured, and backwards.** Step 5 told
the executor to read `trace.schema_chars` "via `/debug-history` or the latency
harness `--json`" and compare with "the M13 baseline". `/debug-history` dumps
history rows and never touches the trace (`pyrrhon/commands/debug_cmd.py:1-35`);
no milestone ever recorded a `schema_chars` baseline. Measured directly against
the installed belt, using the schema shape from `loop.py:271`:

```
    symbol_context:   595 chars      (as specified in Task 3)
 list_dependencies:   407 chars
   find_references:   376 chars
       find_symbol:   343 chars
          repo_map:   313 chars

Task 4 as written  (drop both, add symbol_context):  -188 chars/turn
Task 4 as amended  (drop find_references only):      +219 chars/turn
```

188 chars is ~47 tokens against a belt of ~1.5k — noise, not a result, and not
worth a manual measurement ritual. `schema_chars` is a static property of the
belt anyway, so it belongs in a CI-checked ceiling rather than a one-off reading.
**Resolution:** Step 5 became a deterministic budget test; the milestone claims
the round-trip collapse (3 → 1) and explicitly does not claim a schema saving.

**Not changed:** `find_symbol` stays, for Task 4's original and correct reason —
folding it in forces a source-window read on questions that only wanted a
location.
