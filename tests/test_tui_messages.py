"""Transcript rows in isolation: the rail, and the argument cap.

The rail is the signature of the design, so its glyphs are pinned here
rather than left to whatever the widget happens to render.
"""

from textual.widgets import Markdown, Static

from pyrrhon.tui.messages import (
    ARG_CAP,
    AssistantRow,
    CitationRow,
    InterruptRow,
    NoticeRow,
    ToolRow,
    UserRow,
    summarize_args,
)


def test_every_row_declares_its_rail_glyph():
    """Six glyphs, one column, no other ornament anywhere in the UI."""
    assert UserRow("hi").GLYPH == "▌"
    assert ToolRow("read_file", {}).GLYPH == "┊"
    assert AssistantRow("prose").GLYPH == "│"
    assert CitationRow("a.py:1").GLYPH == "📍"
    assert NoticeRow("hedged").GLYPH == "⚠"
    assert InterruptRow().GLYPH == "⏹"


def test_the_rail_is_a_gutter_not_a_prefix():
    """A glyph prepended to the body would end up in copied text and in
    history; a separate widget cannot."""
    row = UserRow("where is greet defined?")
    body = row.body()
    assert isinstance(body, Static)
    assert "▌" not in str(body.content)


def test_the_two_semantic_hues_are_assigned_by_meaning():
    """Blue means verified, teal means spoken. That mapping is the design."""
    assert UserRow("hi").RAIL == "rail-voice"
    assert AssistantRow("p").RAIL == "rail-evidence"
    assert CitationRow("a.py:1").RAIL == "rail-evidence"
    assert NoticeRow("h").RAIL == "rail-hedge"
    assert ToolRow("read_file", {}).RAIL == "rail-muted"


def test_a_4kb_argument_dict_renders_under_the_cap():
    """Defect 7: one long query or path list used to flood the transcript."""
    args = {"paths": "x" * 4096}
    summary = summarize_args(args)
    assert len(summary) <= ARG_CAP
    assert "…" in summary


def test_the_middle_is_elided_not_the_tail():
    """The end of a path is the part that identifies it."""
    summary = summarize_args({"path": "a" * 40 + "/the_actual_file.py"})
    assert summary.endswith("the_actual_file.py")
    assert summary.startswith("path=a")


def test_short_arguments_are_left_alone():
    assert summarize_args({"path": "a.py"}) == "path=a.py"
    assert summarize_args({}) == ""


def test_an_assistant_row_holds_one_markdown_document():
    row = AssistantRow("first. ")
    assert isinstance(row.body(), Markdown)
    row.append("second.")
    assert row.text == "first. second."


def test_a_tool_row_resolves_to_a_tick():
    row = ToolRow("read_file", {"path": "a.py"})
    assert row.state == "running"
    row.resolve("42 lines", seconds=0.3)
    assert row.state == "ok"
    assert "✓" in str(row.body().content)
    assert "0.3s" in str(row.body().content)


def test_a_failed_tool_row_reads_differently_from_a_successful_one():
    """"It ran" and "it worked" are not the same news."""
    row = ToolRow("read_file", {"path": "nope.py"})
    row.resolve("ERROR: no such file", seconds=0.1)
    assert row.state == "failed"
    assert "✗" in str(row.body().content)


def test_a_long_result_preview_is_capped_too():
    row = ToolRow("grep", {"q": "x"})
    row.resolve("y" * 500, seconds=0.2)
    assert len(str(row.body().content)) < 200


def _text(row: ToolRow) -> str:
    """The Static's content, however this Textual spells it."""
    return str(row._body._Static__content)


def test_a_running_tool_row_takes_a_live_tail_and_a_resolved_one_does_not():
    """A dispatch reports rounds onto the row it already has.

    The second half is the race the callback path makes possible: progress is
    reported from inside the awaited tool call, and a report that lands after
    the call resolved must not overwrite the result with news about how it
    was reached.
    """
    row = ToolRow("explore", {"question": "where is barge-in?"})
    row.progress(2, "grep, read_file")
    assert "round 2 · grep, read_file" in _text(row)

    row.resolve("FOUND: the voice bridge.")
    resolved = _text(row)
    row.progress(3, "read_file")
    assert _text(row) == resolved
