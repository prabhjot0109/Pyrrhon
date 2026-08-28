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

from textual.widgets import Markdown

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.events import SpeechChunk, Transcription
from pyrrhon.tui.app import PyrrhonApp
from tests.helpers import FakeLLM


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
