"""Mountable transcript rows, one widget per kind of thing Pyrrhon says.

These exist because a RichLog is append-only, and a log cannot show work in
progress. A mounted widget can be updated after the fact, which is the single
structural reason a tool row can resolve to a tick, a spinner can tick, and a
three-sentence answer can be one document.

The evidence rail is the signature (spec, Signature). One column of gutter
carrying the epistemic status of the row: what Pyrrhon *said* versus what
Pyrrhon *verified*. It is a separate widget with its own CSS colour, never a
character prepended to the body, so it stays out of copied text and out of
the session history.
"""

from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Markdown, Static

# One long path or query used to flood the transcript (defect 7). The middle
# goes rather than the tail: the end of a path is the part that identifies it.
ARG_CAP = 64


def summarize_args(args: dict) -> str:
    """A one-line argument summary, capped, with the middle elided."""
    text = ", ".join(f"{k}={v}" for k, v in args.items())
    if len(text) <= ARG_CAP:
        return text
    half = (ARG_CAP - 1) // 2
    return f"{text[:half]}…{text[-half:]}"


class Row(Horizontal):
    """A rail gutter plus a body. Every transcript row is one of these."""

    GLYPH = " "
    RAIL = "rail-muted"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("row")

    def body(self) -> Static | Markdown:
        raise NotImplementedError

    def compose(self):
        yield Static(self.GLYPH, classes=f"rail {self.RAIL}")
        yield self.body()


class UserRow(Row):
    """A turn the user took, typed or spoken."""

    GLYPH = "▌"
    RAIL = "rail-voice"

    def __init__(self, text: str, spoken: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        prefix = "🎙 you> " if spoken else "you> "
        self._body = Static(f"{prefix}{text}", classes="body user-body")

    def body(self) -> Static:
        return self._body


class AssistantRow(Row):
    """Assistant prose. One of these per turn, not one per sentence."""

    GLYPH = "│"
    RAIL = "rail-evidence"

    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._buffer = text
        self.markdown = Markdown(text, classes="body")

    def body(self) -> Markdown:
        return self.markdown

    def append(self, fragment: str) -> None:
        """Add prose to this row. The streaming path uses MarkdownStream
        instead; this is for whole content that arrives at once.

        The buffer is the truth and the widget follows it. Updating a Markdown
        widget reaches for the active app, so an unmounted row would raise; the
        buffer is flushed on mount instead.
        """
        self._buffer += fragment
        if self.is_mounted:
            self.markdown.update(self._buffer)

    def on_mount(self) -> None:
        if self._buffer:
            self.markdown.update(self._buffer)

    @property
    def text(self) -> str:
        return self._buffer


class ToolRow(Row):
    """Machinery, not a claim. Mounted on start, resolved on finish."""

    GLYPH = "┊"
    RAIL = "rail-muted"

    def __init__(self, name: str, args: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_name = name
        self.state = "running"
        self._summary = summarize_args(args)
        self._body = Static(self._line(), classes="body tool-body")

    def body(self) -> Static:
        return self._body

    def _line(self, tail: str = "") -> str:
        head = f"{self.tool_name}  {self._summary}" if self._summary else self.tool_name
        return f"{head}{tail}"

    def resolve(self, preview: str, seconds: float | None = None) -> None:
        """Mark the call finished. Failure reads differently from success,
        because "it ran" and "it worked" are not the same news."""
        failed = preview.startswith("ERROR")
        self.state = "failed" if failed else "ok"
        mark = "✗" if failed else "✓"
        elapsed = f"  {seconds:.1f}s" if seconds is not None else ""
        first_line = preview.splitlines()[0] if preview else ""
        if len(first_line) > ARG_CAP:
            first_line = f"{first_line[:ARG_CAP - 1]}…"
        self._body.update(self._line(f"{elapsed}  {mark}  {first_line}"))
        self.set_class(failed, "failed")


class CitationRow(Row):
    """A verified location. The one thing the voice is forbidden to say."""

    GLYPH = "📍"
    RAIL = "rail-evidence"

    def __init__(self, label, **kwargs) -> None:
        super().__init__(**kwargs)
        self._body = Static(label, classes="body citation-body")

    def body(self) -> Static:
        return self._body


class NoticeRow(Row):
    """A hedged or downgraded claim, or an error the user must see."""

    GLYPH = "⚠"
    RAIL = "rail-hedge"

    def __init__(self, text: str, is_error: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._body = Static(text, classes="body notice-body")
        if is_error:
            self.add_class("failed")

    def body(self) -> Static:
        return self._body


class InterruptRow(Row):
    """Where barge-in cut the answer off. History was rewritten to match."""

    GLYPH = "⏹"
    RAIL = "rail-muted"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._body = Static("interrupted", classes="body tool-body")

    def body(self) -> Static:
        return self._body
