"""The face of the product: one banner, rendered by every channel.

The wordmark is stored as *plain* block art and coloured in code. It must
never carry its own ANSI escapes: Rich owns the escape layer, so embedded
sequences get counted as printable columns (a 60-column row measures as
225 and wraps mid-letter) and Rich's own resets land between the ESC and
the `[`, leaving the terminal to print `0;97;1;40m` as literal text.

Colouring is by glyph: the solid blocks are the letter faces, the
box-drawing glyphs are the drop shadow behind them. Two constants below
restyle the whole banner.
"""

from __future__ import annotations

from rich.text import Text

from pyrrhon import __version__

FACE = "#305eff"  # The letter faces, a rich blue. Public because tui/theme.py
                  # inherits it rather than repeating the hex (D6).
_SHADOW = "bold white"
_SHADOW_GLYPHS = frozenset("═║╔╗╚╝")

# 6 rows x 60 columns.
_WORDMARK = """\
██████╗ ██╗   ██╗██████╗ ██████╗ ██╗  ██╗ ██████╗ ███╗   ██╗
██╔══██╗╚██╗ ██╔╝██╔══██╗██╔══██╗██║  ██║██╔═══██╗████╗  ██║
██████╔╝ ╚████╔╝ ██████╔╝██████╔╝███████║██║   ██║██╔██╗ ██║
██╔═══╝   ╚██╔╝  ██╔══██╗██╔══██╗██╔══██║██║   ██║██║╚██╗██║
██║        ██║   ██║  ██║██║  ██║██║  ██║╚██████╔╝██║ ╚████║
╚═╝        ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝\
"""


def banner() -> Text:
    """The banner as a Rich renderable: print it or write it as-is.

    Callers must not wrap this in a style of their own -- an outer style is
    applied across the whole span and flattens the two tones into one.
    """
    text = Text()
    for char in _WORDMARK:
        text.append(char, style=_SHADOW if char in _SHADOW_GLYPHS else FACE)
    # The wordmark is pictorial, so this caption is the only machine-readable
    # "Pyrrhon" in the banner -- for logs, screen readers, and dumb terminals.
    text.append(f"\n  Pyrrhon v{__version__}", style=FACE)
    return text


# The block art is 60 columns wide. Below that plus the splash's own padding
# it wraps mid-glyph, which is defect 11; this is what gets shown instead.
NARROW_COLUMNS = 62


def banner_narrow() -> Text:
    """The wordmark for terminals too narrow for the block art.

    Same two tones as the full banner, so the compact form still reads as the
    same product rather than as a degraded fallback.
    """
    text = Text()
    text.append("PYRRHON", style=f"bold {FACE}")
    text.append(f" v{__version__}", style=_SHADOW)
    return text


def banner_plain() -> str:
    """The same banner with all colour stripped, for logs, tests, and any
    channel that cannot render styles."""
    return banner().plain
