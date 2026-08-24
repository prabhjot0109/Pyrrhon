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
from textual.widgets import Collapsible, Markdown, Static

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


# A result preview is for reassurance, not for reading. The source itself is
# one ctrl+o away, so the row says how it went and gets out of the way.
GIST_CAP = 36


def result_gist(preview: str) -> str:
    """One short line describing a tool result.

    Multi-line output is reported by size rather than pasted: read_file used to
    render `1| def greet(name: str)` into the transcript, which is the code
    viewer coming back through the side door.
    """
    if not preview:
        return ""
    lines = preview.splitlines()
    if len(lines) > 1:
        return f"{len(lines)} lines"
    text = " ".join(lines[0].split())
    return text if len(text) <= GIST_CAP else f"{text[:GIST_CAP - 1]}…"


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
        # No "you>" label. The ▌ rail already says whose turn this is, in the
        # one colour the product reserves for you, and
        # a prefix that repeats the rail is the ornament the spec rules out.
        # 🎙 stays, because typed and spoken are a real distinction.
        self._body = Static(f"🎙 {text}" if spoken else text, classes="body user-body")

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
        self._body.update(self._line(f"{elapsed}  {mark}  {result_gist(preview)}"))
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


class CommandRow(Row):
    """A slash command's answer.

    Machinery, so it wears the tool row's glyph and the muted rail rather than
    a seventh glyph of its own. It went out as a NoticeRow, which is the row
    that means "Pyrrhon could not verify this" — so `/help` and `/plugins`
    arrived under a warning sign in hedge amber, claiming a doubt that has
    nothing to do with listing the command table.
    """

    GLYPH = "┊"
    RAIL = "rail-muted"

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._body = Static(text, classes="body tool-body")

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


class WorkingRow(Row):
    """The one orchestrated moment in the design, and the only animation.

    Between submit and the first chunk the screen used to be frozen with a
    disabled input and nothing else (defect 5). This shows the active tool and
    the elapsed seconds, including before the first tool call, because the
    wait for first-token is the wait users actually notice.
    """

    GLYPH = "┊"
    RAIL = "rail-muted"
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = "thinking"
        self._frame = 0
        self._body = Static("", classes="body tool-body")

    def body(self) -> Static:
        return self._body

    def tick(self, elapsed: float) -> None:
        self._frame = (self._frame + 1) % len(self.FRAMES)
        spinner = self.FRAMES[self._frame]
        self._body.update(f"{spinner} {self.label}  {elapsed:.1f}s")


# A brief longer than this arrives folded. The orientation brief on a real
# repo is a hundred lines of symbol counts, and dumping it over the splash was
# the first thing a new user saw.
FOLD_LINES = 8


class BriefRow(Row):
    """A long ScreenArtifact, folded to one line until asked for.

    Same rail as assistant prose, because it is Pyrrhon telling you something;
    a seventh glyph would break "six glyphs, one column".
    """

    GLYPH = "│"
    RAIL = "rail-evidence"

    def __init__(self, content: str, title: str = "Repo overview", **kwargs) -> None:
        super().__init__(**kwargs)
        self._collapsible = Collapsible(
            Markdown(content, classes="body"),
            title=title,
            collapsed=True,
            classes="body brief-body",
        )

    def body(self) -> Collapsible:
        return self._collapsible


def artifact_row(content: str) -> Row:
    """A short artifact reads inline; a long one folds (defect: the wall)."""
    if content.count(chr(10)) < FOLD_LINES:
        return AssistantRow(content)
    first = content.lstrip().splitlines()[0].lstrip("# ").strip()
    return BriefRow(content, title=first or "Repo overview")
