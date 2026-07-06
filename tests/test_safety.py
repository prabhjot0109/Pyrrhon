"""Safety invariants: the agent cannot execute dangerous commands, by construction.

These tests are a fence, not a feature: they pin the properties that make it
safe to let a voice agent loose on a repo — a frozen tool belt, read-only git
subcommands behind argv-list subprocess calls, one write tool confined to six
filenames under docs/design/, and a read-only deep-subagent belt. If a change
breaks one of these, that change needs a design discussion, not a test edit.
"""

from pathlib import Path

import pytest

from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool
from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES, WriteSpecTool
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM  # scripted-replies double, defined in tests/helpers.py

EXPECTED_BELT = {
    "read_file", "grep", "glob", "remember",
    "find_symbol", "find_references", "list_dependencies", "repo_map",
    "git_log", "git_blame", "git_show",
    "web_search", "web_fetch", "write_spec", "think_deeper",
}

READ_ONLY = EXPECTED_BELT - {"write_spec", "remember", "think_deeper"}


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


def test_no_tool_shells_out_except_the_git_allowlist():
    """Grep-level fence: the only subprocess users in core tools are git.py
    (argv-list, allowlisted subcommands) and nothing else."""
    import pyrrhon.core.tools as tools_pkg

    offenders = []
    for path in Path(tools_pkg.__path__[0]).glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "subprocess" in text and path.name != "git.py":
            offenders.append(path.name)
        if "shell=True" in text:
            offenders.append(f"{path.name} (shell=True)")
    assert offenders == []
