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


def test_import_queries_actually_capture():
    """The import query has to capture the whole statement, because the text
    parser below is what turns it into module names."""
    ts = spec_for_extension(".ts")
    assert 'import { formatName } from "./helpers.js";' in _captures(
        ts, (FIXTURES / "app.ts").read_bytes(), "imports"
    )
    go = spec_for_extension(".go")
    assert 'import "fmt"' in _captures(go, (FIXTURES / "server.go").read_bytes(), "imports")


def test_typescript_import_text_parses_to_a_module():
    spec = spec_for_extension(".ts")
    assert spec.parse_imports('import { formatName } from "./helpers.js";', "") == ["./helpers.js"]


def test_a_commonjs_require_is_an_import_edge_too():
    """require() is how most .js in the wild states a dependency; capturing the
    statement but parsing no module out of it would be a silently dead edge."""
    spec = spec_for_extension(".js")
    source = b'const helpers = require("./helpers.js");\n'
    assert 'require("./helpers.js")' in _captures(spec, source, "imports")
    assert spec.parse_imports('require("./helpers.js")', "") == ["./helpers.js"]


def test_go_import_text_parses_to_a_module():
    spec = spec_for_extension(".go")
    assert spec.parse_imports('import "fmt"', "") == ["fmt"]
    assert spec.parse_imports('import (\n"fmt"\n"os"\n)', "") == ["fmt", "os"]


def test_python_behaviour_is_unchanged():
    spec = spec_for_extension(".py")
    assert spec.parse_imports("from pkg import api", "") == ["pkg", "pkg.api"]
