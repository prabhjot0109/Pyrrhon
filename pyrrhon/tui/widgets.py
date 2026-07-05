"""Custom widgets for the Pyrrhon TUI: the code viewer and the status bar.

Channel code — small sync file reads here are acceptable (the core/ hard
rule about asyncio.to_thread targets core/); M3 revisits if profiling says
otherwise.
"""

from __future__ import annotations

from pathlib import Path

from rich.syntax import Syntax
from textual.widgets import Static

from pyrrhon.core.events import Citation

CONTEXT_LINES = 15  # lines shown above and below the cited line


class CodeViewer(Static):
    """Right-hand pane: syntax-highlighted view of the most recent citation."""

    def __init__(self, **kwargs):
        super().__init__("No citation yet — ask about the code.", **kwargs)
        self.current_file: str | None = None
        self.current_line: int | None = None

    def show(self, citation: Citation, root: Path) -> None:
        path = root / citation.file
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.update(f"ERROR: could not read {citation.file}: {exc}")
            return
        line = citation.line or 1
        # "Centered": a symmetric window around the cited line. The pane's
        # height varies with the terminal, so a fixed window is the stable
        # approximation; the cited line itself is highlighted.
        window = (max(1, line - CONTEXT_LINES), line + CONTEXT_LINES)
        syntax = Syntax(
            source,
            lexer=Syntax.guess_lexer(str(path), code=source),
            line_numbers=True,
            line_range=window,
            highlight_lines={line},
        )
        self.current_file = citation.file
        self.current_line = line
        self.update(syntax)


class StatusBar(Static):
    """One-line status: mode plus the two model slots."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status_text: str = ""

    def show_status(self, mode: str, fast_model: str, deep_model: str) -> None:
        self.status_text = f"mode: {mode} · fast: {fast_model} · deep: {deep_model}"
        self.update(self.status_text)
