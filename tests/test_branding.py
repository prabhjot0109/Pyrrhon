from rich.text import Text

from pyrrhon import __version__
from pyrrhon.branding import _FACE, banner, banner_plain


def test_banner_names_the_product_and_version():
    text = banner_plain()
    # The wordmark is pictorial, so the caption is the only readable name.
    assert "Pyrrhon" in text
    assert __version__ in text


def test_banner_is_a_prestyled_renderable():
    """Callers print it as-is. Raw ANSI must never reach a caller, or Rich
    counts the escapes as columns and wraps mid-letter."""
    assert isinstance(banner(), Text)
    assert "\x1b" not in banner_plain()


def test_wordmark_rows_are_uniform_and_fit_a_terminal():
    """Every wordmark row must measure the same visible width, and the whole
    banner must fit an 80-column terminal without wrapping."""
    rows = [line for line in banner().split("\n") if line.plain.strip()]
    wordmark = rows[:-1]  # last row is the version caption
    assert len({row.cell_len for row in wordmark}) == 1
    assert all(row.cell_len <= 80 for row in rows)


def test_wordmark_is_two_toned():
    """The bevel gives lit top surfaces one colour and the body fill another.
    Both must survive to the caller; a caller's own style would flatten them."""
    styles = {str(span.style) for span in banner().spans}
    # Assert the constant, not a literal hex: the face colour is a design knob
    # (branding.py says "can be changed to any valid Rich colour"), so pinning
    # the hex here only means the test goes red every time someone tunes it.
    # What must hold is that BOTH tones survive to the caller.
    assert _FACE in styles
    assert "bold white" in styles
    assert _FACE != "bold white"
