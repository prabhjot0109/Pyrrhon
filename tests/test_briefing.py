"""M18's opening context: what the model is told about the session it is in.

Three things are worth pinning and the rest is prose. The brief must carry no
coordinate, because a system prompt outlives the read that justified one. The
block must stay under its cap, because it rides every round of every turn. And
an unknown fact must be absent rather than guessed, because the model states
what it is told and "clean working tree" invented from a failed subprocess is
a claim the user has no way to check.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from pyrrhon.core.agent.briefing import (
    MAX_BRIEF_CHARS,
    SessionContext,
    build_repo_brief,
    capture_git_state,
    capture_session_context,
    render_session_context,
)
from pyrrhon.core.grounding.citations import extract_references
from pyrrhon.core.tools.ast_index import SymbolIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def index(tmp_path: Path) -> SymbolIndex:
    """A fresh index over a copy of the fixture repo.

    Copied rather than pointed at the fixture directly: the index writes its
    cache next to the sources, and a test that leaves one behind changes what
    the next pytest run measures.
    """
    root = tmp_path / "repo"
    root.mkdir()
    for source in FIXTURE.glob("*.py"):
        (root / source.name).write_text(source.read_text(encoding="utf-8"))
    return SymbolIndex(root)


async def test_the_brief_carries_no_coordinate(index: SymbolIndex):
    """The invariant, not a request. The repo map renders `name:line` on every
    symbol row, and that shape carries no extension — so the citation regex
    cannot see it, which means the gate cannot see it either. A line number
    the gate is blind to is exactly the one worth removing here."""
    brief = await build_repo_brief(index)
    assert brief, "the fixture repo indexes, so the brief should not be empty"
    assert extract_references(brief) == []
    assert not any(row.strip().split(" ")[-1].isdigit() for row in brief.splitlines())


async def test_the_brief_still_names_the_files(index: SymbolIndex):
    """Stripping coordinates must not strip the point. The brief exists to say
    where to look first, so the file names are the part that has to survive."""
    brief = await build_repo_brief(index)
    assert "Languages:" in brief
    assert ".py" in brief


async def test_the_brief_respects_its_cap(index: SymbolIndex):
    """It rides every round of every turn, so the cap is a latency property
    rather than a tidiness one — the same reasoning as MAX_SOUL_CHARS."""
    assert len(await build_repo_brief(index)) <= MAX_BRIEF_CHARS


async def test_an_unindexable_repo_gets_no_brief(tmp_path: Path):
    """Empty, not an apology. A repo of languages we do not parse is a normal
    case, and telling the model the repo looks empty would be false."""
    (tmp_path / "notes.txt").write_text("nothing to index here")
    assert await build_repo_brief(SymbolIndex(tmp_path)) == ""


def test_render_omits_what_it_does_not_know(tmp_path: Path):
    """None means "we do not know" and never "clean". A guessed tree state is
    worse than silence: the model repeats what it is told, and the user has no
    way to tell an observation from a default."""
    rendered = render_session_context(
        SessionContext(repo_root=tmp_path), mode="understand", voice_active=False
    )
    assert "branch" not in rendered
    assert "working tree" not in rendered
    assert "uncommitted" not in rendered
    assert "What is in this repo" not in rendered


def test_render_carries_the_facts_a_model_cannot_infer(tmp_path: Path):
    """Today's date is the load-bearing one. "What changed last week" is
    unanswerable without it, and a model with no date in context answers from
    its training cutoff and sounds certain doing so."""
    rendered = render_session_context(
        SessionContext(repo_root=tmp_path, branch="m17", dirty=True, brief="X: y"),
        mode="design",
        voice_active=True,
        today=date(2026, 9, 1),
    )
    assert "2026-09-01" in rendered
    assert "branch m17" in rendered
    assert "uncommitted changes" in rendered
    assert "design mode" in rendered
    assert "spoken aloud" in rendered
    assert "X: y" in rendered


def test_the_brief_says_it_is_not_evidence(tmp_path: Path):
    """M18's risk is the mirror of M16e's: a prompt carrying a repo map can
    make the model stop looking and answer from the map. The counterweight
    ships in the same block as the map, the way M16e's did."""
    rendered = render_session_context(
        SessionContext(repo_root=tmp_path, brief="pkg/mod.py:"),
        mode="understand",
        voice_active=False,
    )
    assert "not a tool result" in rendered
    assert "not evidence" in rendered


def test_a_clean_tree_says_so(tmp_path: Path):
    """The other half of the previous case: False is knowledge, not absence,
    and collapsing it into the same silence as None would lose it."""
    rendered = render_session_context(
        SessionContext(repo_root=tmp_path, dirty=False),
        mode="understand",
        voice_active=False,
    )
    assert "clean working tree" in rendered


def test_git_state_of_a_non_repo_is_unknown_not_clean(tmp_path: Path):
    """A directory that is not a repo answers nothing, and the renderer then
    says nothing. The failure path is the common one — Pyrrhon is pointed at
    unpacked source at least as often as at a clone."""
    assert capture_git_state(tmp_path) == (None, None)


def test_git_state_reads_a_real_repo(tmp_path: Path):
    """The success path, against a repo built here rather than against
    Pyrrhon's own, so the assertion does not depend on which branch the suite
    happens to be run from."""
    if not subprocess.run(
        ["git", "--version"], capture_output=True
    ).returncode == 0:  # pragma: no cover - git is a hard dependency of the repo
        pytest.skip("git unavailable")
    for args in (
        ["init", "--initial-branch=trunk"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True)
    (tmp_path / "a.txt").write_text("one")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "one"], capture_output=True
    )
    assert capture_git_state(tmp_path) == ("trunk", False)
    (tmp_path / "a.txt").write_text("two")
    assert capture_git_state(tmp_path) == ("trunk", True)
    # An untracked file is deliberately not dirtiness: walking for untracked
    # files is the expensive half of git status, and a scratch file says
    # nothing about whether what the model reads matches what is committed.
    (tmp_path / "scratch.txt").write_text("ignore me")
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "a.txt"], capture_output=True)
    assert capture_git_state(tmp_path) == ("trunk", False)


async def test_capture_fills_both_halves(index: SymbolIndex):
    """The background task's whole job. Replaced wholesale rather than field
    by field, because the turn loop reads it from another task."""
    before = SessionContext(repo_root=index.root)
    after = await capture_session_context(before, index)
    assert after is not before
    assert after.repo_root == before.repo_root
    assert after.brief
