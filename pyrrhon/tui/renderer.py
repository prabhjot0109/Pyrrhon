"""Core events as mounted transcript rows.

Split out of app.py because they are two jobs: the App owns the shell, the
bindings and the turn worker, while this owns the mapping from the core's
event stream onto widgets. D7 — a file past roughly 150 lines here is doing
two things.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.containers import VerticalScroll

from pyrrhon.channels import EventRenderer
from pyrrhon.core.citation_link import citation_uri
from pyrrhon.core.events import (
    AskUser,
    Citation,
    ScreenArtifact,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
    Transcription,
    TruncateSpeech,
    TurnFinished,
    VoiceNotice,
)
from pyrrhon.core.grounding.gate import HEDGE, LINE_HEDGE, LINE_UNSEEN_HEDGE
from pyrrhon.tui.messages import (
    AssistantRow,
    CitationRow,
    InterruptRow,
    NoticeRow,
    ToolRow,
    UserRow,
    artifact_row,
)

if TYPE_CHECKING:
    from pyrrhon.tui.app import PyrrhonApp


def split_hedge(text: str) -> tuple[str, str]:
    """Separate a gate hedge from the prose it was appended to.

    The gate appends one of three sentences to the speech when a reference
    could not be verified. On screen those are a different kind of claim from
    the prose, so they leave the paragraph and take the ⚠ rail. Imported from
    the gate rather than restated here, so a reworded hedge cannot drift.
    """
    for hedge in (HEDGE, LINE_HEDGE, LINE_UNSEEN_HEDGE):
        if text.endswith(hedge):
            return text[: -len(hedge)].rstrip(), hedge
        if text == hedge:
            return "", hedge
    return text, ""


class TuiRenderer(EventRenderer):
    """Core events as mounted transcript rows.

    Composition rather than inheritance on the App: PyrrhonApp already
    subclasses Textual's App, and mixing a second base into a Textual widget
    invites metaclass surprises for no gain.
    """

    def __init__(self, app: "PyrrhonApp"):
        self._app = app
        # Which turn the last utterance opened. TurnFinished carries no
        # payload — "which turn" is deliberately the consumer's bookkeeping —
        # so this is where the answer is kept.
        self._spoken_generation: int | None = None

    @property
    def _transcript(self) -> VerticalScroll:
        return self._app.query_one("#transcript", VerticalScroll)

    def _mount(self, row) -> None:
        # The turn owns row ordering, so the spinner stays at the foot of
        # the column without every hook knowing about it.
        self._app.turn.mount(row)

    async def on_transcription(self, event: Transcription) -> None:
        """What STT heard, and the turn boundary for the voice path.

        On the same rail as a typed turn so voice and text read the same in
        the transcript; the 🎙 marks it as spoken input.

        A spoken turn is started by the bridge, which cannot reach the screen
        except through events, so this is the one place the previous turn is
        sealed. Without it every spoken answer after the first was appended to
        the first one's row — one paragraph holding an answer, a tool filler,
        an apology and a later answer, with the user rows below it empty.

        Awaited (see `EventRenderer.render_awaited`) rather than deferred: the
        rotation has to happen before the events that arrived behind this one,
        and a second `call_later` would land after them. The row is mounted
        after the rotation, so it lands under the previous turn's prose rather
        than inside it.
        """
        await self._app.begin_turn()
        self._spoken_generation = self._app.turn.generation
        self._mount(UserRow(event.text, spoken=True))

    def on_voice_notice(self, event: VoiceNotice) -> None:
        self._mount(NoticeRow(event.text, is_error=event.is_error))
        self._app.notify(
            event.text, severity="error" if event.is_error else "information"
        )

    def on_speech(self, event: SpeechChunk) -> None:
        """One row per turn, not one per chunk (defect 2).

        The core splits into sentences whenever voice is active, and this used
        to render a whole Rich Markdown document per sentence, each with its
        own padding. The first chunk of a turn opens a MarkdownStream; the
        rest write into it.
        """
        text = event.text
        prose, hedge = split_hedge(text)
        if prose:
            self._app.turn.stream_speech(prose)
        if hedge:
            # A hedge is a different epistemic claim from the prose it trails,
            # so it gets the ⚠ rail rather than a restyled span inside it.
            self._mount(NoticeRow(hedge))

    def on_tool_started(self, event: ToolCallStarted) -> None:
        self._app.turn.set_working_label(event.name)
        row = ToolRow(event.name, event.args)
        self._app.turn.track_tool(event.name, row)
        self._mount(row)

    def on_tool_finished(self, event: ToolCallFinished) -> None:
        """Resolve the row on_tool_started created rather than mounting a
        second one — the events arrive in pairs on one turn (defect 6)."""
        row = self._app.turn.claim_tool(event.name)
        if row is None:
            # A finish with no start: render it rather than drop it, which is
            # the failure this hook exists to end.
            self._mount(ToolRow(event.name, {}))
            return
        row.resolve(event.result_preview, self._app.turn.elapsed(row))
        # The working row collapses into the row that just resolved.
        self._app.turn.set_working_label("thinking")

    def on_citation(self, event: Citation) -> None:
        # Two independent routes to the same line, so a terminal without
        # OSC 8 and a machine without $EDITOR each still have one (D2).
        uri = citation_uri(self._app.repo_root, event)
        label = f"{event.file}:{event.line}" if event.line else event.file
        self._mount(CitationRow(Text(label, style=f"link {uri}" if uri else "")))
        self._app.record_citation(event)

    def on_artifact(self, event: ScreenArtifact) -> None:
        # M14's orientation brief is the first real emitter, and on a real repo
        # it is a hundred lines of symbol counts that used to land on top of
        # the splash. Long artifacts arrive folded; short ones read inline.
        self._mount(artifact_row(event.content))

    def on_question(self, event: AskUser) -> None:
        # Design mode's Socratic question (spec: M6). Pyrrhon speaking, so the
        # assistant rail; bold is what marks it as a question needing an answer.
        # A seventh glyph would break "six glyphs, one column".
        self._mount(AssistantRow(f"**{event.question}**"))

    async def on_interrupted(self, event: TruncateSpeech) -> None:
        """Voice barge-in: history was rewritten to what was actually heard.

        And the turn ends here, because that is what the user just did. The
        core has already truncated the assistant message to the prose that was
        actually played; leaving the view open left the spinner turning and
        the status bar saying "speaking" until the next utterance rotated it,
        which is a screen claiming Pyrrhon is still talking over a user who
        interrupted precisely because they wanted it to stop.

        The row is mounted first, so the marker lands under the prose it cut
        off rather than under the spinner that is about to be removed.
        """
        self._mount(InterruptRow())
        await self._app.end_turn()

    async def on_turn_finished(self, event: TurnFinished) -> None:
        """The bridge reporting that a spoken turn is over.

        It closes the turn the *utterance* opened, not whatever happens to be
        open now. Reading the current generation here would be no guard at
        all — it always matches. A spoken turn can end after something else
        has taken the screen: the plainest case is a question typed while
        voice is running, which opens a turn of its own that this report has
        nothing to say about.
        """
        generation, self._spoken_generation = self._spoken_generation, None
        if generation is None:
            return  # nothing spoken yet; not ours to close
        await self._app.end_turn(generation)

