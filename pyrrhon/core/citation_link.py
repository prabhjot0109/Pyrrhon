"""Turn a Citation into something the terminal can open.

Pyrrhon's answers point at path:line instead of pasting source (TEXT_STYLE), so
the pointer has to be worth following. Modern terminals — Windows Terminal,
iTerm2, VS Code, WezTerm, kitty — render OSC 8 hyperlinks, which Rich emits for
`[link=…]`; the ones that don't simply show the text unchanged, so there is no
capability check to get wrong and no degraded case to handle.

`file://` URIs carry no standard line anchor. The `#L<n>` suffix is what VS
Code and most editors accept, and a terminal that hands the bare path to the OS
still opens the right file — the worst case is landing on line 1.
"""

from __future__ import annotations

from pathlib import Path

from pyrrhon.core.events import Citation


def citation_uri(repo_root: Path, citation: Citation) -> str | None:
    """A file:// URI for `citation`, or None if it escapes the repo.

    The path came from model output that the grounding gate has already
    bounded, but this is the step that hands it to the user's shell, so the
    containment check is repeated here rather than assumed.
    """
    root = repo_root.resolve()
    try:
        target = (root / citation.file).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    uri = target.as_uri()
    return f"{uri}#L{citation.line}" if citation.line else uri


def citation_markup(repo_root: Path, citation: Citation) -> str:
    """Rich markup for one citation line: clickable where supported."""
    label = f"{citation.file}:{citation.line}" if citation.line else citation.file
    uri = citation_uri(repo_root, citation)
    if uri is None:
        return f"[green]📍 {label}[/green]"
    return f"[green]📍 [link={uri}]{label}[/link][/green]"
