"""The voice path's on-screen lifecycle.

Voice turns enter through `_on_voice_event`, not through the prompt, so none
of the typed-path suites cover them. Every defect these tests pin shipped
green: one `TurnView` is created in `PyrrhonApp.__init__` and bracketed only
inside `_agent_turn`, which voice never reaches.

Events are pushed in synchronously here, back to back, which is the harshest
ordering the channel can be handed — a real bridge spaces them across awaits.
Pinning the burst is deliberate: the guarantee under test is "rendered in the
order they happened", and a guarantee that only holds when the events are slow
is not one.
"""

from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Markdown

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.events import (
    SpeechChunk,
    Transcription,
    TruncateSpeech,
    TurnFinished,
)
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.messages import WorkingRow
from tests.helpers import FakeLLM, settle


def make_app(repo_root: Path) -> PyrrhonApp:
    # repo_root must be disposable (the `sample_repo` fixture): mounting the
    # TUI warms the symbol index, which writes .pyrrhon/cache.db.
    agent = build_agent(repo_root, llm=FakeLLM([]), home=repo_root.parent)
    return PyrrhonApp(repo_root=repo_root, agent=agent)


def speech_rows(app: PyrrhonApp) -> list:
    return list(app.query("AssistantRow.speech"))


def row_text(row) -> str:
    """The prose in a transcript row, whichever body widget it uses.

    UserRow's body is a Static and AssistantRow's a Markdown, and the two
    spell their content differently. The rail glyph is a separate widget, so
    nothing here has to strip it.
    """
    body = row.body()
    return body.source if isinstance(body, Markdown) else str(body.content)


async def close_last_turn(app: PyrrhonApp, pilot) -> None:
    """End the turn still in flight, so its row holds everything it said.

    `TurnView` treats the MarkdownStream as a rendering optimisation and its
    own buffer as the document, reconciling the two when the turn ends — so
    an open turn's row is the only one whose `source` may still be behind.
    In a live session Task 3's `TurnFinished` does this; here the test says
    plainly that the turn is over.
    """
    await app.end_turn()
    await pilot.pause()


async def test_each_spoken_turn_gets_its_own_row(sample_repo: Path):
    """Defect A: turn two's prose landed inside turn one's row."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app._on_voice_event(Transcription(text="Are you listening?"))
        app._on_voice_event(SpeechChunk(text="Absolutely."))
        await pilot.pause()

        app._on_voice_event(Transcription(text="How does search work?"))
        app._on_voice_event(SpeechChunk(text="It is a regex scan."))
        await pilot.pause()
        await close_last_turn(app, pilot)

        rows = speech_rows(app)
        assert len(rows) == 2, f"one row per spoken turn, got {len(rows)}"
        assert "Absolutely." in rows[0].markdown.source
        assert "regex scan" in rows[1].markdown.source
        assert "regex scan" not in rows[0].markdown.source


async def test_the_user_row_sits_above_its_own_answer(sample_repo: Path):
    """The order on screen is the order the events arrived.

    This is the test that catches a turn boundary scheduled behind the events
    it is meant to precede: the rows all exist, and read in the wrong order.
    """
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app._on_voice_event(Transcription(text="First question?"))
        app._on_voice_event(SpeechChunk(text="First answer."))
        await pilot.pause()
        app._on_voice_event(Transcription(text="Second question?"))
        app._on_voice_event(SpeechChunk(text="Second answer."))
        await pilot.pause()
        await close_last_turn(app, pilot)

        joined = " | ".join(
            row_text(w) for w in app.query("UserRow, AssistantRow.speech")
        )
        assert joined.index("First question") < joined.index("First answer")
        assert joined.index("First answer") < joined.index("Second question")
        assert joined.index("Second question") < joined.index("Second answer")


async def test_barge_in_ends_the_turn_it_interrupted(sample_repo: Path):
    """Defect B. The row-splitting half of this is already handled by the
    rotation on the *next* utterance, so what is left is the interval in
    between: from the interruption until the user speaks again, the spinner
    was still turning and the status bar still said Pyrrhon was speaking.

    A barge-in is the user ending the turn. Nothing about it is pending.
    """
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app._on_voice_event(Transcription(text="How does this work?"))
        app._on_voice_event(SpeechChunk(text="Looking for the right files."))
        await pilot.pause()
        assert app.turn.speaking, "prose is in flight"

        app._on_voice_event(TruncateSpeech(played_text="Looking for the right"))
        await pilot.pause()

        assert not app.turn.speaking, "the turn ended when the user cut in"
        assert not list(app.query(WorkingRow)), "nothing is still working"
        assert list(app.query("InterruptRow")), "the interruption is still shown"


async def test_the_answer_after_a_barge_in_starts_a_new_row(sample_repo: Path):
    """And the prose that was cut off keeps only what was said before it."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app._on_voice_event(Transcription(text="How does this work?"))
        app._on_voice_event(SpeechChunk(text="Looking for the right files."))
        await pilot.pause()
        app._on_voice_event(TruncateSpeech(played_text="Looking for the right"))
        await pilot.pause()

        app._on_voice_event(Transcription(text="Why is that?"))
        app._on_voice_event(SpeechChunk(text="Because there is no index."))
        await pilot.pause()
        await close_last_turn(app, pilot)

        rows = speech_rows(app)
        assert len(rows) == 2, f"barge-in must seal the row, got {len(rows)}"
        assert "no index" not in rows[0].markdown.source


async def test_the_status_bar_stops_saying_speaking(sample_repo: Path):
    """Defect C: turn.speaking is `self._speech_stream is not None`, and
    nothing on the voice path ever cleared it — so the status bar read
    "speaking" for the rest of the session after the first spoken answer."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        app._on_voice_event(Transcription(text="Are you listening?"))
        app._on_voice_event(SpeechChunk(text="Absolutely."))
        await pilot.pause()
        assert app.turn.speaking, "prose is in flight"

        app._on_voice_event(TurnFinished())
        await pilot.pause()
        assert not app.turn.speaking, "the turn is over"
        assert not list(app.query(WorkingRow)), "and the spinner stopped"


async def test_a_late_turn_finished_cannot_close_the_turn_that_replaced_it(
    sample_repo: Path,
):
    """The generation guard.

    The bridge cancels a turn without awaiting it (`_start_turn`,
    `_on_interruption`), so a spoken turn's end-of-turn report can arrive
    after something else has already opened a turn on screen — a typed
    question while voice is running is the plain case. Reading the *current*
    generation when the report arrives would be no guard at all, so the
    renderer remembers which turn the utterance opened and closes that one.
    """
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        before = app.turn.generation
        app._on_voice_event(Transcription(text="A spoken question?"))
        app._on_voice_event(SpeechChunk(text="A spoken answer."))
        # BOTH events, not just the rotation: the premise is that the
        # utterance's turn is older than the typed one AND already holds the
        # answer. A speech chunk still queued lands on the typed turn instead
        # and takes its working row down, which is the assertion below.
        await settle(
            pilot,
            lambda: app.turn.generation != before and app.turn.speaking,
            "the spoken turn to open and start speaking",
        )
        spoken = app.turn.generation

        # The turn is replaced — what a typed turn's begin_turn() does.
        await app.begin_turn()
        assert app.turn.generation != spoken, "a new turn is on screen"

        app._on_voice_event(TurnFinished())
        # Waited on the hook CONSUMING the report, not on one pause. The
        # renderer takes `_spoken_generation` and leaves None behind, so that
        # is the observable "the guard has now run" — and without it a pass
        # could mean the report simply had not arrived, which is the same
        # screen state for the opposite reason.
        await settle(
            pilot,
            lambda: app._renderer._spoken_generation is None,
            "the end-of-turn report to be consumed",
        )
        # The TurnView's own row, not a global query. `finish()` removes a row
        # without awaiting it, so a query over the whole app can see a row that
        # is on its way out or miss one on its way in; the turn knows which row
        # is ITS row.
        assert app.turn.working_row is not None, "the newer turn is still working"


async def test_a_turn_finished_still_closes_its_own_turn(sample_repo: Path):
    """The other half of the guard: it must not refuse the normal case."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        before = app.turn.generation
        app._on_voice_event(Transcription(text="A question?"))
        app._on_voice_event(SpeechChunk(text="An answer."))
        app._on_voice_event(TurnFinished())
        await settle(
            pilot,
            lambda: app.turn.generation != before and not app.turn.speaking,
            "the spoken turn to open and close",
        )
        assert not app.turn.speaking
        assert not list(app.query(WorkingRow))


@pytest.mark.parametrize("size", [(80, 24), (200, 50)])
async def test_a_spoken_session_reads_the_same_at_either_terminal_size(
    sample_repo: Path, size: tuple[int, int]
):
    """The Definition-of-Done box that was never ticked, made durable.

    The TUI redesign's DoD asked for the channel to be driven by hand at 80x24
    and at 200x50 *with voice on*, and it never was — which is the whole
    reason three defects shipped green. A hand-drive is a one-time answer to a
    question that comes back on every change, so the geometries are pinned
    here as well.
    """
    app = make_app(sample_repo)
    async with app.run_test(size=size) as pilot:
        for n in (1, 2):
            app._on_voice_event(Transcription(text=f"Question {n}?"))
            app._on_voice_event(SpeechChunk(text=f"Answer {n}."))
            await pilot.pause()
            app._on_voice_event(TurnFinished())
            await pilot.pause()

        rows = speech_rows(app)
        assert len(rows) == 2
        assert not app.turn.speaking, "the spinner and the status bar recovered"

        transcript = app.query_one("#transcript", VerticalScroll)
        # D1 again, under voice: no sibling pane, and no horizontal margin on
        # a child of the column silently narrowing the transcript.
        assert transcript.outer_size.width == size[0]
        assert transcript.scroll_offset.x == 0, "the column never scrolls sideways"
