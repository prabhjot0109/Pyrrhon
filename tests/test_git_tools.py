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
