"""M19's session commands: /clear /compact /cost /export /sessions.

Every handler here returns a string and never raises, which the registry
requires, so what these tests mostly check is the awkward cases: a channel
with no session, a provider that reports no usage, a compaction that has
nothing to do. Those are the paths a user hits first and the ones an
integration test would never reach.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrrhon.commands import session_cmd  # noqa: F401 - registers the commands
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.events import Citation
from pyrrhon.core.providers.llm import SessionSpend, TokenUsage
from pyrrhon.core.session import Session
from pyrrhon.core.telemetry import TurnTrace
from pyrrhon.core.transcript import Transcript


class FakeAgent:
    def __init__(self, budget: int | None = 1000):
        self.voice_active = False
        self.mode = "understand"
        self.system_prompt = "SYSTEM"
        self.last_trace = None
        self.token_scale = 1.0
        self.context_keep_last = 8
        self.spend = SessionSpend()
        self.known_context_budget = budget
        self.llm = None
        self._budget = budget

    def request_budget(self, schema_chars: int) -> int:
        return self._budget or 0


class Recorder:
    def notify(self, text: str) -> None: ...


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def context(repo: Path, session: Session | None, agent: FakeAgent) -> CommandContext:
    return CommandContext(repo_root=repo, agent=agent, ui=Recorder(), session=session)


async def run(line: str, ctx: CommandContext) -> str:
    answer = await dispatch(line, ctx)
    assert answer is not None, f"{line} did not dispatch"
    return answer


async def test_every_command_refuses_politely_without_a_session(repo: Path):
    """A channel can legitimately have no session — the headless one runs its
    own. A handler that assumed otherwise would raise inside dispatch, which
    the registry contract forbids."""
    ctx = context(repo, None, FakeAgent())
    for line in ("/clear", "/compact", "/export"):
        assert (await run(line, ctx)).startswith("ERROR:")


async def test_clear_reports_what_it_dropped(repo: Path):
    agent = FakeAgent()
    session = Session(agent)
    ctx = context(repo, session, agent)
    assert "already empty" in await run("/clear", ctx)
    session.history = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    assert "Cleared 2 message(s)" in await run("/clear", ctx)
    assert session.history == []


async def test_compact_declines_when_the_thread_already_fits(repo: Path):
    """A user asking to compact a history that fits should be told it fits,
    not charged for a summarization round trip that frees nothing."""
    agent = FakeAgent(budget=100000)
    session = Session(agent)
    session.history = [{"role": "user", "content": "short"}]
    answer = await run("/compact", context(repo, session, agent))
    assert "Nothing to do" in answer


async def test_compact_runs_the_ladder_and_reports_a_number(repo: Path, monkeypatch):
    """The whole point of exposing it: compaction was invisible, so the answer
    has to be a measurement rather than a reassurance."""
    agent = FakeAgent(budget=10)
    session = Session(agent)
    session.history = [{"role": "user", "content": "x" * 4000}]

    async def fake_fit(history, llm, budget, **kwargs):
        history[:] = [{"role": "user", "content": "x"}]
        return "summarize"

    monkeypatch.setattr("pyrrhon.commands.session_cmd.fit_to_budget", fake_fit)
    answer = await run("/compact", context(repo, session, agent))
    assert "'summarize' rung" in answer
    assert "down to" in answer


async def test_compact_says_so_when_nothing_has_established_a_budget(repo: Path):
    """None is a real answer here for the same reason it is on the status
    meter: compacting against a denominator nobody measured is a guess wearing
    a number's clothes."""
    agent = FakeAgent(budget=None)
    answer = await run("/compact", context(repo, Session(agent), agent))
    assert answer.startswith("ERROR:")


async def test_cost_counts_requests_even_when_tokens_are_unknown(repo: Path):
    """A local server that omits `usage` still consumed a request against
    whatever ceiling the user is watching. A count that silently undercounts
    is worse than one that admits what it does not know."""
    agent = FakeAgent()
    agent.spend.add(None)
    agent.spend.add(None)
    answer = await run("/cost", context(repo, Session(agent), agent))
    assert "2 request(s)" in answer
    assert "reports no token usage" in answer


async def test_cost_reports_the_totals_and_names_what_it_cannot_see(repo: Path):
    """The two-ceilings trap in one line: per-request limits are the only ones
    a provider advertises, and this account's daily budget appears in no
    header at all."""
    agent = FakeAgent(budget=8000)
    agent.spend.add(TokenUsage(prompt=1200, completion=300, total=1500))
    answer = await run("/cost", context(repo, Session(agent), agent))
    assert "1500 tokens" in answer
    assert "1200 in, 300 out" in answer
    assert "about 8000 tokens per request" in answer
    assert "daily or monthly budget" in answer


async def test_cost_says_nothing_yet_before_the_first_request(repo: Path):
    agent = FakeAgent()
    assert "Nothing spent yet" in await run("/cost", context(repo, Session(agent), agent))


async def test_export_writes_under_the_pyrrhon_directory(repo: Path, tmp_path: Path):
    """The fence says nothing writes outside .pyrrhon/, and a command that
    drops files where the user runs `git status` is a surprise even when it is
    permitted."""
    agent = FakeAgent()
    transcript = Transcript.start(repo, home=tmp_path)
    session = Session(agent, transcript=transcript)
    session._pending = ("why?", "because.", (Citation(file="a.py", line=3),))
    answer = await run("/export", context(repo, session, agent))
    written = repo / ".pyrrhon" / "exports" / f"{transcript.session_id}.md"
    assert written.is_file()
    assert str(written) in answer
    body = written.read_text(encoding="utf-8")
    # The pending turn was flushed first: exporting a walkthrough without its
    # last answer is the one thing this must not do.
    assert "why?" in body and "because." in body and "`a.py:3`" in body


async def test_export_takes_an_explicit_destination(repo: Path, tmp_path: Path):
    agent = FakeAgent()
    session = Session(agent, transcript=Transcript.start(repo, home=tmp_path))
    session.transcript.record("q?", "a")
    target = tmp_path / "out" / "walkthrough.md"
    await run(f"/export {target}", context(repo, session, agent))
    assert target.is_file()


async def test_export_refuses_when_nothing_is_being_saved(repo: Path):
    agent = FakeAgent()
    answer = await run("/export", context(repo, Session(agent), agent))
    assert answer.startswith("ERROR:")
    assert "not being saved" in answer


async def test_sessions_lists_what_resume_takes(repo: Path, monkeypatch, tmp_path: Path):
    agent = FakeAgent()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    transcript = Transcript.start(repo)
    transcript.record("the first question?", "an answer")
    answer = await run("/sessions", context(repo, Session(agent), agent))
    assert transcript.session_id in answer
    assert "the first question?" in answer
    assert "--resume" in answer


async def test_sessions_is_honest_about_an_empty_list(repo: Path, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    agent = FakeAgent()
    answer = await run("/sessions", context(repo, Session(agent), agent))
    assert "No saved sessions" in answer


def test_spend_separates_requests_from_tokens():
    """They run out at different times: a per-minute REQUEST ceiling can block
    a session with plenty of token allowance left, which reads as a mystery
    when only tokens are on screen."""
    spend = SessionSpend()
    spend.add(TokenUsage(prompt=10, completion=5, total=15))
    spend.add(None)
    assert (spend.requests, spend.prompt, spend.completion, spend.total) == (2, 10, 5, 15)


def test_a_trace_bearing_turn_budgets_against_its_own_schemas(repo: Path):
    """/compact asks request_budget the same question the turn's pre-flight
    asked, schemas netted out. A looser question here would report a saving
    the next turn does not get."""
    agent = FakeAgent(budget=500)
    session = Session(agent)
    trace = TurnTrace()
    trace.schema_chars = 4000
    session.last_turn_trace = trace
    assert agent.request_budget(trace.schema_chars) == 500
