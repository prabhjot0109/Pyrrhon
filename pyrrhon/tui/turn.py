"""Everything on screen that belongs to one turn, and nothing that outlives it.

Split out of the App because it is a second job (D7). The App owns the shell,
the bindings and the worker; this owns the widgets a turn creates and the
bookkeeping that resolves them — the single prose row, the spinner, and the
tool rows waiting to be told how they went.

Its whole contract is that `finish()` runs on every exit path, including
abort, so nothing here survives into the next turn.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.widgets import Markdown

from pyrrhon.tui.messages import AssistantRow, ToolRow, WorkingRow

if TYPE_CHECKING:
    from pyrrhon.tui.app import PyrrhonApp


class TurnView:
    """The on-screen state of the turn in flight."""

    def __init__(self, app: "PyrrhonApp") -> None:
        self._app = app
        self.working_row: WorkingRow | None = None
        self._speech_row: AssistantRow | None = None
        self._speech_stream = None
        self._timer = None
        self._started: float = 0.0
        # A FIFO per tool name, because M10 dispatches in parallel and the
        # same tool can be in flight twice on one turn.
        self._pending: dict[str, list[tuple[ToolRow, float]]] = {}
        self._started_at: dict[ToolRow, float] = {}

    @property
    def _transcript(self) -> VerticalScroll:
        return self._app.query_one("#transcript", VerticalScroll)

    @property
    def speaking(self) -> bool:
        """True while prose is actually being produced. The status bar's
        difference between listening and speaking."""
        return self._speech_stream is not None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Awaited, and that is load-bearing.

        Widget.mount() is asynchronous. Left un-awaited, the working row was
        still pending when the turn's first event arrived, so `is_mounted` was
        False, that row skipped the `before=` insertion and was appended after
        the spinner — and once the spinner was removed it sat at the bottom of
        the turn instead of the top. Awaiting the mount is what makes the
        transcript read in the order the events arrived.
        """
        self._started = time.monotonic()
        self.working_row = WorkingRow()
        await self._transcript.mount(self.working_row)
        self._timer = self._app.set_interval(0.1, self._tick)

    async def finish(self) -> None:
        """Every exit path, including abort. A stranded spinner claims
        Pyrrhon is still working when it is not."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self.working_row is not None:
            self.working_row.remove()
            self.working_row = None
        await self._end_speech_stream()
        self._pending.clear()
        self._started_at.clear()

    def _tick(self) -> None:
        if self.working_row is not None:
            self.working_row.tick(time.monotonic() - self._started)
        # Cheap because the field is reactive: an unchanged value repaints
        # nothing, so polling this ten times a second costs nothing.
        self._app.refresh_voice_state()

    # -- prose -------------------------------------------------------------

    def stream_speech(self, text: str) -> None:
        """Append prose to this turn's single assistant row (defect 2).

        The first chunk mounts the row and opens the stream; the rest write
        into it. MarkdownStream coalesces high-frequency appends, which is
        what removes the defect without the core changing its splitter.
        """
        if self._speech_row is None:
            # Tagged so the turn's prose is distinguishable from an artifact
            # row: a background ScreenArtifact can land mid-turn, and "one
            # document per turn" is a claim about the prose, not the screen.
            self._speech_row = AssistantRow(classes="speech")
            self.mount(self._speech_row)
            self._speech_stream = Markdown.get_stream(self._speech_row.markdown)
        self._app.call_later(self._speech_stream.write, text)

    async def _end_speech_stream(self) -> None:
        stream, self._speech_stream, self._speech_row = self._speech_stream, None, None
        if stream is None:
            return
        try:
            await stream.stop()
        except asyncio.CancelledError:
            # Not our cancellation. MarkdownStream.stop() cancels its own task
            # and awaits it; the task suppresses the CancelledError and returns
            # normally, which is precisely the case where asyncio marks that
            # task cancelled anyway and re-raises here. Letting it escape the
            # turn's finally leaves the prompt disabled forever.
            #
            # A real abort still gets through: esc cancels the worker, and a
            # pending cancellation on it is what cancelling() reports.
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise

    # -- rows --------------------------------------------------------------

    def mount(self, row) -> None:
        """Mount a transcript row above the working row, so the spinner stays
        at the foot of the column.

        `is_mounted` is the right question rather than `is not None`: start()
        awaits the spinner's mount, so a pending working row here means the
        turn has already finished and the row belongs at the end.
        """
        working = self.working_row
        if working is not None and working.is_mounted:
            self._transcript.mount(row, before=working)
        else:
            self._transcript.mount(row)

    def set_working_label(self, label: str) -> None:
        if self.working_row is not None:
            self.working_row.label = label

    def track_tool(self, name: str, row: ToolRow) -> None:
        self._pending.setdefault(name, []).append((row, time.monotonic()))

    def claim_tool(self, name: str) -> ToolRow | None:
        """The oldest unresolved row for this tool. Calls finish in order."""
        queue = self._pending.get(name)
        if not queue:
            return None
        row, started = queue.pop(0)
        self._started_at[row] = started
        return row

    def elapsed(self, row: ToolRow) -> float | None:
        started = self._started_at.pop(row, None)
        return None if started is None else time.monotonic() - started
