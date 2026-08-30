"""Safety invariants: the agent cannot execute dangerous commands, by construction.

These tests are a fence, not a feature: they pin the properties that make it
safe to let a voice agent loose on a repo — a frozen tool belt, an allowlist
of modules that may spawn a subprocess (argv-list only, never a shell), one
write tool confined to six filenames under docs/design/, and a read-only
deep-subagent belt. If a change breaks one of these, that change needs a
design discussion, not a test edit.
"""

from pathlib import Path

import pytest

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.agent.policy import policy_for
from pyrrhon.core.agent.turn_type import REPO_QUESTION
from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool
from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES, WriteSpecTool
from tests.helpers import FakeLLM  # scripted-replies double, defined in tests/helpers.py

EXPECTED_BELT = {
    "read_file", "read_image", "grep", "glob", "remember",
    "find_symbol", "symbol_context", "list_dependencies", "repo_map",
    "git_log", "git_blame", "git_show",
    "web_search", "web_fetch", "write_spec", "think_deeper",
}

READ_ONLY = EXPECTED_BELT - {"write_spec", "remember", "think_deeper"}

# What think_deeper actually gets. Narrower than READ_ONLY: the web tools are
# read-only with respect to the repo, but a subagent loop is excluded from
# them on cost grounds, not safety ones — repo questions stay in the repo.
EXPECTED_DEEP_BELT = READ_ONLY - {"web_search", "web_fetch"}


@pytest.fixture
def agent(tmp_path):
    # home=tmp_path: isolate from the developer's real ~/.pyrrhon/plugins —
    # a global plugin contributing tools would break the exact-belt assertion.
    return build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)


def test_the_tool_belt_is_exactly_the_reviewed_set(agent):
    assert set(agent.tools) == EXPECTED_BELT


def test_deep_subagent_belt_is_read_only(agent):
    deep = agent.tools["think_deeper"]
    assert set(deep.tools) <= READ_ONLY


def test_deep_subagent_belt_is_exactly_the_read_only_belt(agent):
    """Not just a subset — the whole of it.

    The subset check above catches a write tool sneaking into the deep belt.
    It cannot catch the opposite drift: a read-only tool added to the main
    belt and forgotten in the deep one, which silently leaves think_deeper
    less capable than the loop that escalates to it. While the two belts were
    hand-maintained side by side that was a live hazard with no failing test.
    build_agent now derives one from the other, and this pins the result.
    """
    deep = agent.tools["think_deeper"]
    assert set(deep.tools) == EXPECTED_DEEP_BELT


def test_deep_belt_does_not_inherit_mcp_or_plugin_tools(agent, tmp_path):
    """Derivation filters the builtin belt, not the assembled one — an MCP
    server or plugin must not widen the deep subagent's reach by arriving."""

    class _Extra(WriteSpecTool):
        name = "mcp__probe"

    widened = build_agent(
        tmp_path,
        llm=FakeLLM([]),
        deep_llm=FakeLLM([]),
        home=tmp_path,
        extra_tools=[_Extra(tmp_path)],
    )
    assert "mcp__probe" in widened.tools
    assert set(widened.tools["think_deeper"].tools) == EXPECTED_DEEP_BELT


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


# The belt's schema rides on every tool-bearing turn, so its size is a latency
# property, not a style one. Pinned as a ceiling rather than an equality: a
# tool description may be reworded, but the belt may not quietly double.
#
# Measured 7087 chars over 15 tools after symbol_context's description was
# rewritten to gate on knowing an exact identifier (595 -> 790). That is ~49
# extra tokens on every tool-bearing turn, spent to remove up to two whole
# model round trips — the trade the milestone exists to make.
#
# 7534 over 16 after M15b added read_image, so the ceiling moves with the belt
# rather than being absorbed by the newcomer. read_image's schema is 447 chars
# against a 471-char belt average: trimming a below-average description until
# a ceiling calibrated for 15 tools still fits would optimise the number, not
# the latency, and would leave the new tool worse described than every peer.
# ~29 extra tokens per tool-bearing turn is what a sixteenth capability costs.
# What this must still catch is the belt QUIETLY doubling; the headroom here
# (~6%) matches what the 15-tool ceiling left.
#
# 8067 over the same 16 after M16c closed every schema with
# `additionalProperties: false`. That is 31 chars a tool, ~124 tokens across
# the belt, and it is bought with a round: an unrecognised argument is now
# refused by the provider's own validator instead of reaching run(), raising
# TypeError, and costing a full round trip to discover. repo_map's "takes no
# arguments" line is the other 33 chars, and it exists to stop the ATTEMPT
# rather than the call.
MAX_BELT_SCHEMA_CHARS = 8500


def test_the_belt_schema_stays_within_its_latency_budget(agent):
    full = policy_for(REPO_QUESTION, voice_active=False)
    total = sum(len(str(s)) for s in agent._tool_schemas(full) or ())
    assert total <= MAX_BELT_SCHEMA_CHARS, (
        f"belt schema grew to {total} chars; every tool-bearing turn pays this"
    )


async def test_git_show_rejects_flag_injection(tmp_path):
    tool = GitShowTool(tmp_path)
    for evil in ("--output=/tmp/pwn", "-p", ""):
        assert "ERROR" in await tool.run(ref=evil)


async def test_git_tools_reject_paths_outside_the_repo(tmp_path):
    log = GitLogTool(tmp_path)
    assert "outside the repo" in await log.run(path="../../etc/passwd")
    blame = GitBlameTool(tmp_path)
    assert "outside the repo" in await blame.run(path="../secrets.txt")


async def test_write_spec_only_writes_the_six_artifacts(tmp_path):
    tool = WriteSpecTool(tmp_path)
    for evil in ("../../evil.md", "PRD.md/../../../evil.md", ".bashrc", "evil.md"):
        result = await tool.run(filename=evil, content="x")
        assert "ERROR" in result
    assert not (tmp_path.parent / "evil.md").exists()
    ok = await tool.run(filename="PRD.md", content="# ok")
    assert "PRD.md" in ok
    assert (tmp_path / "docs" / "design" / "PRD.md").read_text(encoding="utf-8") == "# ok"
    assert set(SPEC_FILENAMES) == {"PRD.md", "HLD.md", "LLD.md", "api.md", "database.md", "risks.md"}


# Modules in pyrrhon/core/tools/ permitted to spawn a subprocess. Both use
# argv-list create_subprocess_exec with cwd pinned to the repo root; neither
# ever builds a command string. Widened from {git.py} to include repo.py in
# M10, when grep moved onto ripgrep — the design discussion the docstring
# above asks for, not a test edit of convenience. Adding to this set requires
# the same discussion.
SUBPROCESS_ALLOWLIST = {"git.py", "repo.py"}


def test_no_tool_shells_out_except_the_allowlist():
    """Grep-level fence: only allowlisted modules may touch subprocess, and
    nothing anywhere may reach a shell."""
    import pyrrhon.core.tools as tools_pkg

    offenders = []
    for path in Path(tools_pkg.__path__[0]).glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "subprocess" in text and path.name not in SUBPROCESS_ALLOWLIST:
            offenders.append(path.name)
        for forbidden in ("shell=True", "create_subprocess_shell", "os.system", "os.popen"):
            if forbidden in text:
                offenders.append(f"{path.name} ({forbidden})")
    assert offenders == []


def test_grep_argv_is_flag_safe():
    """The user-supplied pattern must only ever appear after a literal `--`,
    so a pattern beginning with '-' is data and never an option."""
    from pyrrhon.core.tools.repo import GrepTool

    tool = GrepTool(Path("."))
    argv = tool._rg_argv(
        "/usr/bin/rg",
        "--color=always",       # a pattern that is also a real rg flag
        tool._root,
        glob="*.py",
        ignore_case=True,
        context_lines=2,
    )
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert argv[0] == "/usr/bin/rg"                 # resolved binary, never "rg ..."
    separator = argv.index("--")
    assert argv[separator + 1] == "--color=always"  # pattern sits after `--`
    assert "--color=always" not in argv[:separator]  # and nowhere before it


async def test_grep_rejects_paths_outside_the_repo(tmp_path):
    from pyrrhon.core.tools.repo import GrepTool

    tool = GrepTool(tmp_path)
    assert "outside the repo" in await tool.run(pattern="x", path="../../etc")


async def test_grep_pattern_starting_with_a_dash_is_searched_not_parsed(tmp_path):
    """End-to-end proof of the argv fence, through whichever backend is live."""
    from pyrrhon.core.tools.repo import GrepTool

    (tmp_path / "flags.txt").write_text("run with --color=never here\n", encoding="utf-8")
    result = await GrepTool(tmp_path).run(pattern="--color=never")
    assert "flags.txt:1:" in result


async def test_web_fetch_refuses_internal_addresses():
    """A model that reads an untrusted repo picks the URL. The metadata
    endpoint must never be reachable through a Pyrrhon tool."""
    from pyrrhon.core.tools.web import WebFetchTool

    for evil in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "http://[::1]/",
    ):
        assert "ERROR" in await WebFetchTool().run(url=evil)


def test_the_text_style_does_not_invite_pasted_source():
    """M14: answers point at path:line instead of reprinting the file.

    The reader has the repo open and the citation is clickable, so a fenced
    copy of what is already on disk costs tokens and latency and buys nothing.
    Pinned because the previous wording explicitly welcomed code snippets, and
    this is a deliberate product decision rather than a phrasing preference.
    """
    from pyrrhon.core.agent.prompts import TEXT_STYLE

    assert "Do NOT paste source code back at the reader" in TEXT_STYLE
    assert "code snippets are welcome" not in TEXT_STYLE


def test_symbol_context_is_gated_on_knowing_an_identifier(agent):
    """Steering the model here unconditionally sends concepts to a tool that
    answers 'No definition found', spending a round to learn nothing. The
    description must name both the trigger and the grep fall-back."""
    description = agent.tools["symbol_context"].description
    assert "exact identifier" in description
    assert "grep" in description


# -- The layering rule, enforced rather than documented -----------------------

CHANNEL_PACKAGES = ("tui", "voice", "repl", "commands", "cli")


def _module_level_imports(source: str) -> list[str]:
    """Names imported at module scope. Function-local imports are excluded on
    purpose: they are the documented escape hatch, because what the rule
    protects is that core/ and config/ stay IMPORTABLE without a channel."""
    import ast

    tree = ast.parse(source)
    names = []
    for node in tree.body:  # top level only, never ast.walk
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_core_and_config_take_no_import_time_dependency_on_a_channel():
    """CLAUDE.md calls this the one thing that must stay true, and until now it
    was checked by a grep someone had to remember to run.

    The pipecat exception in core/providers/adapters.py has had a test since
    M15b; the rule that exception is an exception TO did not. This is that
    test, and it is the reason a doc grep is documentation and not enforcement.
    """
    import pyrrhon.config
    import pyrrhon.core

    banned = {f"pyrrhon.{name}" for name in CHANNEL_PACKAGES}
    offenders = []
    for package in (pyrrhon.core, pyrrhon.config):
        root = Path(package.__file__).parent
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for name in _module_level_imports(source):
                if name in banned or any(name.startswith(b + ".") for b in banned):
                    offenders.append(f"{path.relative_to(root.parent)}: {name}")
    assert not offenders, (
        "core/ and config/ must import no channel at module scope:\n"
        + "\n".join(offenders)
    )


def test_the_escape_hatch_this_rule_allows_is_still_the_only_one():
    """config/catalog.py reads the voice table inside a function so that a menu
    render pays for it and an import does not. Pinned because the previous
    check was an unanchored grep that matched this line and therefore looked
    like a violation — which is how a correct exception gets 'fixed'."""
    import inspect

    from pyrrhon.config import catalog

    assert "from pyrrhon.voice.registry import" in inspect.getsource(catalog._providers)
    assert "from pyrrhon.voice" not in catalog.__dict__.get("__doc__", "")
