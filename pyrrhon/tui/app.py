"""The Textual TUI — the second channel over the headless core (M2).

Layout: transcript (left) · code viewer (right) · status bar · input.
Agent turns run in a Textual worker so this event loop never blocks —
from M3 the same loop carries audio (spec: real-time discipline).
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, RichLog

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation
from pyrrhon.tui.widgets import CodeViewer, StatusBar


class PyrrhonApp(App):
    TITLE = "Pyrrhon"

    CSS = """
    #panes {
        height: 1fr;
    }
    #transcript {
        width: 3fr;
        padding: 0 1;
    }
    CodeViewer {
        width: 2fr;
        border-left: solid $accent;
        padding: 0 1;
    }
    StatusBar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, repo_root: Path, agent: Agent):
        super().__init__()
        self.repo_root = repo_root
        self.agent = agent
        self.history: list[dict] = []
        self.last_citation: Citation | None = None
        self.last_command_response: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            yield RichLog(id="transcript", wrap=True, markup=True)
            yield CodeViewer(id="code-viewer")
        yield StatusBar(id="status-bar")
        yield Input(placeholder="Ask about the repo — or /help", id="prompt")

    def on_mount(self) -> None:
        self.refresh_status()
        self.query_one("#prompt", Input).focus()
        self.query_one("#transcript", RichLog).write(
            f"Pyrrhon — discussing {self.repo_root.name}. Type /help for commands."
        )

    def show_citation(self, citation: Citation) -> None:
        """Record the citation and jump the code viewer to it."""
        self.last_citation = citation
        self.query_one(CodeViewer).show(citation, self.repo_root)

    def refresh_status(self) -> None:
        fast = getattr(self.agent.llm, "model", "unknown")
        deep = getattr(getattr(self.agent, "deep_llm", None), "model", "= fast")
        self.query_one(StatusBar).show_status("understand", fast, deep)
