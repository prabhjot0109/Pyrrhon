import asyncio
from pathlib import Path

from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool, _ripgrep

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_read_file_returns_numbered_lines():
    out = await ReadFileTool(FIXTURE).run(path="utils/helpers.py")
    assert "    1| def greet(name: str) -> str:" in out


async def test_read_file_rejects_escape_and_missing():
    tool = ReadFileTool(FIXTURE)
    assert (await tool.run(path="../outside.txt")).startswith("ERROR:")
    assert (await tool.run(path="nope.py")).startswith("ERROR:")


async def test_grep_reports_posix_path_line_and_text():
    out = await GrepTool(FIXTURE).run(pattern=r"def greet")
    assert "utils/helpers.py:1: def greet(name: str) -> str:" in out


async def test_grep_bad_regex_is_an_error_string():
    assert (await GrepTool(FIXTURE).run(pattern="(unclosed")).startswith("ERROR:")


async def test_glob_lists_matching_files():
    out = await GlobTool(FIXTURE).run(pattern="**/*.py")
    assert "app.py" in out and "utils/helpers.py" in out
    assert "README.md" not in out


async def test_glob_absolute_pattern_is_an_error_string():
    assert (await GlobTool(FIXTURE).run(pattern="/etc/*")).startswith("ERROR:")


async def test_tool_schema_shape():
    schema = ReadFileTool(FIXTURE).schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert "path" in schema["function"]["parameters"]["properties"]


# -- grep: new options and rg/fallback parity (M10 Stage 2.3) ---------------
#
# GrepTool prefers ripgrep and keeps the pure-Python scan as a fallback. Both
# paths are held to the SAME semantics deliberately: rg honours .gitignore and
# skips hidden files by default, so without --no-ignore --hidden and explicit
# SKIP_DIRS excludes, search results would silently depend on whether rg
# happened to be installed on the machine.

import pytest

from pyrrhon.core.tools import repo as repo_module


@pytest.fixture
def grep_repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "alpha.py").write_text(
        "import os\n\n\ndef target():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "beta.txt").write_text("target here too\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("TARGET in caps\n", encoding="utf-8")
    # A gitignored-looking build dir: the Python scan searches it, so rg must
    # too, or the two backends disagree.
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("target generated\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    # A skipped dir: neither backend may look inside.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("target vendored\n", encoding="utf-8")
    return tmp_path


async def both_backends(tool: "repo_module.GrepTool", **kwargs) -> tuple[str, str]:
    """Run a query through ripgrep and through the pure-Python fallback."""
    with_rg = await tool.run(**kwargs)
    saved = repo_module._RG_PATH
    repo_module._RG_PATH = None  # force the fallback
    try:
        without_rg = await tool.run(**kwargs)
    finally:
        repo_module._RG_PATH = saved
    return with_rg, without_rg


@pytest.mark.parametrize(
    "query",
    [
        {"pattern": "target"},
        {"pattern": "target", "glob": "*.py"},
        {"pattern": "target", "path": "pkg"},
        {"pattern": "TARGET", "ignore_case": True},
        {"pattern": "target", "context_lines": 2},
        {"pattern": "no_such_thing_anywhere"},
    ],
)
async def test_ripgrep_and_the_fallback_agree(grep_repo: Path, query: dict):
    if repo_module._ripgrep() is None:
        pytest.skip("ripgrep not installed")
    with_rg, without_rg = await both_backends(GrepTool(grep_repo), **query)
    assert with_rg == without_rg


async def test_grep_searches_gitignored_files(grep_repo: Path):
    """Parity requires --no-ignore: the Python scan has no notion of
    .gitignore, so rg must not apply one either."""
    with_rg, without_rg = await both_backends(GrepTool(grep_repo), pattern="generated")
    assert "build/gen.py" in with_rg
    assert with_rg == without_rg


async def test_grep_never_descends_into_skip_dirs(grep_repo: Path):
    with_rg, without_rg = await both_backends(GrepTool(grep_repo), pattern="vendored")
    assert with_rg == "No matches."
    assert without_rg == "No matches."


async def test_grep_glob_filters_by_filename(grep_repo: Path):
    result = await GrepTool(grep_repo).run(pattern="target", glob="*.txt")
    assert "beta.txt" in result
    assert "alpha.py" not in result


async def test_grep_path_narrows_the_search(grep_repo: Path):
    result = await GrepTool(grep_repo).run(pattern="target", path="pkg")
    assert "pkg/alpha.py" in result
    assert "build/gen.py" not in result


async def test_grep_context_lines_mark_matches_distinctly(grep_repo: Path):
    """The 'path:N' prefix stays uniform so every line remains citable; only
    the trailing separator differs — ':' for a match, '-' for context — which
    is how the model tells which line actually matched."""
    result = await GrepTool(grep_repo).run(
        pattern="def target", path="pkg/alpha.py", context_lines=1
    )
    assert "pkg/alpha.py:4: def target():" in result
    assert "pkg/alpha.py:5- return 1" in result
    assert "pkg/alpha.py:3-" in result


async def test_grep_reports_a_bad_regex_rather_than_raising(grep_repo: Path):
    with_rg, without_rg = await both_backends(GrepTool(grep_repo), pattern="[")
    # The wording comes from whichever engine ran; the contract is the prefix.
    assert with_rg.startswith("ERROR: bad regex")
    assert without_rg.startswith("ERROR: bad regex")


@pytest.mark.skipif(_ripgrep() is None, reason="ripgrep not installed")
async def test_repeated_greps_never_report_a_spurious_failure(tmp_path):
    """The returncode race is timing-dependent: run it enough times that a
    kill-before-reap would show up at least once."""
    (tmp_path / "a.py").write_text("needle = 1\n", encoding="utf-8")
    tool = GrepTool(tmp_path)
    for _ in range(25):
        result = await tool.run(pattern="needle")
        assert "ERROR" not in result, result
        assert "a.py:1:" in result


@pytest.mark.skipif(_ripgrep() is None, reason="ripgrep not installed")
async def test_a_genuinely_bad_regex_still_reports_an_error(tmp_path):
    result = await GrepTool(tmp_path).run(pattern="(unclosed")
    assert "ERROR" in result


@pytest.mark.skipif(_ripgrep() is None, reason="ripgrep not installed")
async def test_a_search_that_finished_on_its_own_is_never_killed(tmp_path, monkeypatch):
    """The deterministic half of the returncode race.

    `returncode` stays None until the child is waited on, so a clean stdout EOF
    looks identical to "still running" — and the old `finally` killed on both.
    The corruption that follows (a kill signal read as a failed search) is
    timing-dependent and will not reproduce on demand, but the kill itself
    fired on every successful grep. That is what this pins.
    """
    (tmp_path / "a.py").write_text("needle = 1\n", encoding="utf-8")
    killed: list[str] = []
    real_exec = asyncio.create_subprocess_exec

    async def spy_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        real_kill = proc.kill

        def recording_kill():
            killed.append("kill")
            real_kill()

        monkeypatch.setattr(proc, "kill", recording_kill)
        return proc

    monkeypatch.setattr(repo_module.asyncio, "create_subprocess_exec", spy_exec)
    result = await GrepTool(tmp_path).run(pattern="needle")

    assert "a.py:1:" in result
    assert killed == [], "a search that reached EOF on its own must not be killed"


# -- a re-read costs nothing (M16c) ------------------------------------------


def _seen(**ranges):
    """A stand-in for EvidenceLedger.covered, keyed by path."""
    return lambda path: ranges.get(path.replace("\\", "/"), [])


async def test_a_fully_covered_range_returns_a_note_not_the_bytes(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(f"line {n}" for n in range(1, 101)), encoding="utf-8")
    tool = ReadFileTool(tmp_path, seen=_seen(**{"a.py": [(1, 100)]}))
    out = await tool.run(path="a.py", start_line=40, end_line=90)
    assert "already shown" in out
    assert "line 40" not in out


async def test_a_partial_overlap_returns_only_the_uncovered_part(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(f"line {n}" for n in range(1, 101)), encoding="utf-8")
    tool = ReadFileTool(tmp_path, seen=_seen(**{"a.py": [(1, 40)]}))
    out = await tool.run(path="a.py", start_line=1, end_line=60)
    assert "line 41" in out
    assert "line 40" not in out
    assert "1-40 already shown" in out


async def test_an_interior_hit_is_never_carved_out_of_the_window(tmp_path):
    """A grep records the single lines it matched. Trimming those from the
    middle would split the window in two, and a hit shown out of context is
    not the same as having read around it."""
    (tmp_path / "a.py").write_text("\n".join(f"line {n}" for n in range(1, 101)), encoding="utf-8")
    tool = ReadFileTool(tmp_path, seen=_seen(**{"a.py": [(40, 40)]}))
    out = await tool.run(path="a.py", start_line=1, end_line=60)
    assert "line 40" in out
    assert "already shown" not in out


async def test_a_different_file_is_unaffected(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(f"line {n}" for n in range(1, 101)), encoding="utf-8")
    (tmp_path / "b.py").write_text("\n".join(f"line {n}" for n in range(1, 101)), encoding="utf-8")
    tool = ReadFileTool(tmp_path, seen=_seen(**{"a.py": [(1, 100)]}))
    out = await tool.run(path="b.py", start_line=1, end_line=10)
    assert "line 5" in out


async def test_without_an_accessor_nothing_is_suppressed(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(f"line {n}" for n in range(1, 11)), encoding="utf-8")
    out = await ReadFileTool(tmp_path).run(path="a.py")
    assert "line 5" in out
