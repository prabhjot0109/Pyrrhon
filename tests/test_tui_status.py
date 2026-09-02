"""Phase 4: the instruments. Defects 8, 9 and 14.

Token accounting has existed since M15b and no channel ever read it; voice
state has never been on screen in a voice-first product.
"""

from pathlib import Path

from pyrrhon.bootstrap import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.status import StatusBar
from tests.helpers import FakeLLM


def make_app(repo: Path) -> PyrrhonApp:
    agent = build_agent(repo, llm=FakeLLM([]), home=repo.parent)
    return PyrrhonApp(repo_root=repo, agent=agent)


def test_every_status_field_is_reactive():
    """Defect 14: the bar used to rebuild its whole content on every update."""
    reactives = StatusBar._reactives
    for field in ("mode", "fast_model", "context_pct", "voice_state", "latency_ms"):
        assert field in reactives, f"{field} should repaint on its own"


async def test_the_context_meter_tracks_history_growth(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.agent.context_budget_tokens = 1000
        app.session.history.clear()
        app.refresh_status()
        await pilot.pause()
        empty = app.query_one(StatusBar).context_pct

        app.session.history.append({"role": "user", "content": "x" * 8000})
        app.refresh_status()
        await pilot.pause()
        grown = app.query_one(StatusBar).context_pct

        assert empty is not None and grown is not None
        assert grown > empty, f"{grown}% should exceed {empty}%"
        assert "ctx" in app.query_one(StatusBar).status_text


async def test_no_budget_shows_no_meter_rather_than_zero(sample_repo: Path):
    """An unknown is not an empty context window."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.agent.context_budget_tokens = 0
        app.refresh_status()
        await pilot.pause()
        assert app.query_one(StatusBar).context_pct is None
        assert "ctx" not in app.query_one(StatusBar).status_text


async def test_voice_state_is_off_until_the_pipeline_runs(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.refresh_status()
        await pilot.pause()
        assert app.voice_state() == "off"
        # Off is the one state that renders nothing: a mic icon that is always
        # there says nothing about whether the mic is open.
        assert "🎙" not in app.query_one(StatusBar).status_text


class _RunningTask:
    """A stand-in for the voice pipeline's task. VoiceController.running is
    `self._task is not None and not self._task.done()`, so this is enough —
    and it patches an instance rather than the class, which a previous version
    of this test got wrong and left VoiceController without its property."""

    def done(self) -> bool:
        return False


async def test_voice_state_distinguishes_listening_from_speaking(sample_repo: Path):
    """Derived from what the channel already knows, with no new core event."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app.voice._task = _RunningTask()
        assert app.voice.running
        assert app.voice_state() == "listening"

        app.turn._speech_stream = object()   # prose is being produced
        assert app.voice_state() == "speaking"
        app.refresh_status()
        await pilot.pause()
        assert "🎙 speaking" in app.query_one(StatusBar).status_text

        app.turn._speech_stream = None
        app.voice._task = None


async def test_the_spinner_timer_survives_a_teardown_with_a_turn_open(
    sample_repo: Path,
):
    """The 100ms working-row timer outlives the widget tree.

    `TurnView.finish()` stops it, and nothing calls `finish()` when the app
    tears down mid-answer — which is what `/exit` or ctrl+c during a turn does.
    A tick landing in that window used to find the screen emptied and raise
    `NoMatches`, printing a traceback over the user's terminal on the way out.

    Reproduced directly rather than by racing the timer: the failure was a
    query against a screen with no StatusBar on it, so removing the bar and
    ticking is the same call in the same state, every run.
    """
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.begin_turn()
        await pilot.pause()
        app.query_one(StatusBar).remove()
        await pilot.pause()
        # The tick, exactly as the timer issues it. Must not raise.
        app.refresh_voice_state()
