"""symbol_context: definition + source + references + import edges in one round."""

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


async def test_the_window_does_not_license_lines_it_never_showed(tmp_path):
    """The other half of the M13 rule: recording a range wider than what was
    printed would hand the model a licence to cite unseen lines."""
    from pyrrhon.core.grounding.evidence import EvidenceLedger

    (tmp_path / "big.py").write_text(
        "def target():\n    pass\n" + "# filler\n" * 200, encoding="utf-8"
    )
    tool = SymbolContextTool(SymbolIndex(tmp_path), tmp_path)
    result = await tool.run(name="target", context_lines=5)

    ledger = EvidenceLedger()
    ledger.record_tool_result("symbol_context", {"name": "target"}, result)
    assert ledger.observed("big.py", 1)
    assert not ledger.observed("big.py", 150)


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
    assert result.count("c0.py") >= 1       # the listed sites are still listed
    # The cap is declared AND localised. Asserting merely that "c2.py" appears
    # somewhere would pass on the `imported by:` line below, which says nothing
    # about call sites — the rollup has to name where the hidden ones are.
    assert "…and 10 more in c2.py (10)" in result


async def test_it_works_on_a_non_python_language(tmp_path, polyglot_repo):
    """The whole point of Tasks 1-2: this tool is language-agnostic because the
    index is."""
    tool = SymbolContextTool(SymbolIndex(polyglot_repo), polyglot_repo)
    result = await tool.run(name="formatName")
    assert "helpers.js:1" in result
    assert "export function formatName" in result
    assert "app.ts" in result  # called from the TypeScript file
