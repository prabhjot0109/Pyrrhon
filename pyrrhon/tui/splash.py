"""The banner, as a splash rather than a logged line (defects 11 and 12).

Written into the scrolling log it wrapped mid-glyph below 62 columns and
scrolled away within one turn. As a widget it is sized to the terminal and
cleared by the first real turn.

Everything here is a Text object rather than a string, which is the structural
fix for defect 12: a repo directory named `weird[repo]` was parsed as Rich
markup on its way into a markup=True sink, and a Text has no markup pass to
fall into.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from pyrrhon.branding import NARROW_COLUMNS, banner, banner_narrow


def splash_text(repo_root: Path, width: int) -> Text:
    """The wordmark for this terminal width, plus the orientation line."""
    wordmark = banner() if width >= NARROW_COLUMNS else banner_narrow()
    content = Text()
    content.append_text(wordmark)
    content.append(chr(10) * 2)
    content.append(f"Discussing {repo_root.name}. Type /help for commands.")
    return content
