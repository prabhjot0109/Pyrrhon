"""M19's persistence: what a session leaves behind, and what it deliberately does not.

The load-bearing assertion in this file is the one about coordinates. A
persisted transcript outlives the reads that justified a line number by days
rather than by turns, so a resumed session that could cite from memory would
be the worst version of the failure M16e closed: a stale in-range line that
verifies perfectly and is wrong. The shape of the data is what prevents it,
not an instruction the model may ignore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_references
from pyrrhon.core.session import Session, open_session
from pyrrhon.core.transcript import (
    Transcript,
    list_sessions,
    repo_slug,
    resolve_session,
    sessions_dir,
)


class FakeAgent:
    """Enough Agent for a Session that never runs a turn."""

    def __init__(self):
        self.voice_active = False
        self.mode = "understand"
        self.system_prompt = "SYSTEM"
        self.last_trace = None
        self.token_scale = 1.0


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "someone-elses-repo"
    root.mkdir()
    return root


def test_a_recorded_turn_comes_back_as_a_chat_history(repo: Path, tmp_path: Path):
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("where does it cache?", "On write, in the client.")
    assert transcript.messages() == [
        {"role": "user", "content": "where does it cache?"},
        {"role": "assistant", "content": "On write, in the client."},
    ]


def test_restored_prose_carries_no_coordinate(repo: Path, tmp_path: Path):
    """The invariant the whole design rests on. M16e already strips a gated
    answer on the way into history; doing it again on the way out of the file
    makes it a property of the transcript rather than one inherited from
    somewhere else — and the transcript is the copy that survives a reboot."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record(
        "where is it?", "It caches on write at httpx/_client.py:475, before the send."
    )
    restored = transcript.messages()
    assert extract_references(restored[1]["content"]) == []
    # The path survives, because it is the part that stays true and costs one
    # read to re-anchor. Only the line goes.
    assert "httpx/_client.py" in restored[1]["content"]


def test_citations_are_kept_beside_the_answer_not_inside_it(repo: Path, tmp_path: Path):
    """Stripping the prose must not throw the evidence away — /export is the
    reason the citations are persisted at all, and re-inserting them inline
    would mean guessing which sentence each one belonged to."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("where?", "In the client.", (Citation(file="a/b.py", line=12),))
    markdown = transcript.to_markdown(repo)
    assert "`a/b.py:12`" in markdown
    assert "In the client." in markdown


def test_a_truncated_line_is_skipped_rather_than_fatal(repo: Path, tmp_path: Path):
    """The one time a line is half-written is a crash mid-write, and that is
    exactly the session someone is trying to recover."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("one?", "first")
    with transcript.path.open("a", encoding="utf-8") as handle:
        handle.write('{"question": "two?", "answ\n')
    transcript.record("three?", "third")
    assert [e.question for e in transcript.entries()] == ["one?", "three?"]


def test_two_sessions_started_in_the_same_second_do_not_share_a_file(
    repo: Path, tmp_path: Path
):
    """The random suffix is not paranoia. Two channels opened at once is an
    ordinary thing to do, and the second appending to the first would
    interleave two conversations into one resume."""
    first = Transcript.start(repo, home=tmp_path)
    second = Transcript.start(repo, home=tmp_path)
    assert first.path != second.path


def test_two_clones_of_one_project_keep_separate_histories(tmp_path: Path):
    """The name alone collides — everybody has more than one `api` checkout —
    and a bare hash makes the directory unbrowsable. Both."""
    left = tmp_path / "work" / "api"
    right = tmp_path / "fork" / "api"
    for path in (left, right):
        path.mkdir(parents=True)
    assert repo_slug(left) != repo_slug(right)
    assert repo_slug(left).startswith("api-")
    assert sessions_dir(left, tmp_path) != sessions_dir(right, tmp_path)


def test_sessions_list_newest_first_and_skip_the_empty_ones(
    repo: Path, tmp_path: Path
):
    """An empty session is noise in a list you are choosing from, and there is
    always one of them: the session that opened, saved nothing and exited."""
    old = Transcript.start(repo, home=tmp_path)
    old.record("older question?", "older answer")
    Transcript.start(repo, home=tmp_path)  # opened, never used
    new = Transcript(old.path.with_name("29990101-000000-ffff.jsonl"))
    new.record("newer question?", "newer answer")
    listed = list_sessions(repo, tmp_path)
    assert [info.session_id for info in listed] == [new.session_id, old.session_id]
    assert listed[0].preview == "newer question?"


def test_resume_matches_an_id_by_prefix(repo: Path, tmp_path: Path):
    """An id nobody can retype is an id nobody uses, so the random suffix that
    exists only to prevent a collision never has to be typed."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("q?", "a")
    stem = transcript.session_id
    assert resolve_session(repo, stem[:8], tmp_path) == transcript.path
    assert resolve_session(repo, None, tmp_path) == transcript.path
    assert resolve_session(repo, "nothing-like-this", tmp_path) is None


def test_the_transcript_agrees_with_history_about_what_was_heard(
    repo: Path, tmp_path: Path
):
    """Barge-in truncation rewrites the last assistant message AFTER the turn
    is over, which is why a record is written at the start of the next turn.
    A transcript that disagreed with history would be the one artifact
    claiming Pyrrhon said something the user never let it finish."""
    session = Session(FakeAgent(), transcript=Transcript.start(repo, home=tmp_path))
    session.history = [{"role": "assistant", "content": "half a sen"}]
    session._pending = ("go on?", "half a sentence that was cut off", ())
    session.close()
    assert session.transcript.entries()[0].answer == "half a sen"


def test_clear_forgets_the_thread_and_keeps_the_record(repo: Path, tmp_path: Path):
    """/clear is about what the MODEL carries. Destroying what the user has
    already been told is a different operation and nobody asked for it."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("earlier?", "earlier answer")
    session = Session(FakeAgent(), transcript=transcript)
    session.history = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    assert session.clear() == 2
    assert session.history == []
    assert session.transcript.entries()


def test_open_session_says_so_when_a_resume_finds_nothing(repo: Path, monkeypatch):
    """Silently starting empty is how a user loses an afternoon believing they
    are continuing it."""
    monkeypatch.setattr("pyrrhon.core.session.resolve_session", lambda *a, **k: None)
    monkeypatch.setattr(
        "pyrrhon.core.session.Transcript.start", lambda *a, **k: Transcript(Path("x"))
    )
    _session, notice = open_session(FakeAgent(), repo, resume="")
    assert "starting a new one" in notice


def test_no_save_leaves_nothing_behind(repo: Path, tmp_path: Path):
    session, notice = open_session(FakeAgent(), repo, save=False, home=tmp_path)
    assert session.transcript is None
    assert notice == ""
    assert not sessions_dir(repo, tmp_path).exists()


def test_a_resumed_session_restores_prose_and_no_system_message(
    repo: Path, tmp_path: Path
):
    """Agent._run_turn rewrites history[0] on every turn, so a restored system
    message would be overwritten anyway — and until it was, the session would
    run on a prompt built by a different version of Pyrrhon."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("first?", "first answer")
    transcript.record("second?", "second answer")
    session = Session(FakeAgent())
    assert session.resume(transcript) == 2
    assert [m["role"] for m in session.history] == [
        "user", "assistant", "user", "assistant"
    ]
    assert session.transcript is transcript


def test_covered_ground_lists_questions_not_conclusions(repo: Path, tmp_path: Path):
    """The questions, and that is what makes this cheap enough to exist. A
    summary of what was concluded needs an LLM call and goes stale against a
    repo that moved; the questions are what the user asked, they stay true,
    and they are what someone scans for "where was I"."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("how does the cache work?", "It clears on write.")
    transcript.record("what calls it?", "The client, on send.")
    covered = transcript.covered_ground()
    assert "- how does the cache work?" in covered
    assert "- what calls it?" in covered
    assert covered.index("cache") < covered.index("calls"), "oldest first"
    assert "clears on write" not in covered


def test_covered_ground_is_capped_and_says_what_it_dropped(repo: Path, tmp_path: Path):
    """A list long enough to scroll is one nobody reads — and a silently
    truncated one is worse than a short one, because it reads as complete."""
    transcript = Transcript.start(repo, home=tmp_path)
    for index in range(20):
        transcript.record(f"question {index}?", "answer")
    covered = transcript.covered_ground(limit=5)
    assert "20 turn(s)" in covered
    assert "…15 earlier turn(s)" in covered
    assert "question 19?" in covered
    assert "question 3?" not in covered


def test_an_empty_session_has_no_covered_ground(repo: Path, tmp_path: Path):
    assert Transcript.start(repo, home=tmp_path).covered_ground() == ""


def test_a_resume_hands_back_the_ground_it_covered(repo: Path, tmp_path: Path):
    """"Where was I" is the first thing a returning user needs and the last
    thing they will type a command to find out, so it rides the notice."""
    transcript = Transcript.start(repo, home=tmp_path)
    transcript.record("where does it cache?", "On write.")
    _session, notice = open_session(
        FakeAgent(), repo, resume=transcript.session_id, home=tmp_path
    )
    assert "Resumed" in notice
    assert "where does it cache?" in notice
