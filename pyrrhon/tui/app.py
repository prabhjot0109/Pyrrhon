"""The Textual TUI — the second channel over the headless core (M2).

Layout: transcript (left) · code viewer (right) · status bar · input.
Agent turns run in a Textual worker so this event loop never blocks —
from M3 the same loop carries audio (spec: real-time discipline).
"""

from __future__ import annotations

from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, RichLog

from pyrrhon.commands import builtin, debug_cmd  # noqa: F401 — registers commands
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import (
    Citation,
    ScreenArtifact,
    SpeechChunk,
    ToolCallStarted,
)
from pyrrhon.core.providers.llm import MissingAPIKeyError
from pyrrhon.core.session import Session
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
        self.session = Session(agent)
        self.last_citation: Citation | None = None
        self.last_command_response: str | None = None

    @property
    def agent(self) -> Agent:
        return self.session.agent

    @property
    def history(self) -> list[dict]:
        return self.session.history

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

    @on(Input.Submitted, "#prompt")
    async def on_prompt_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        transcript = self.query_one("#transcript", RichLog)
        transcript.write(Text(f"you> {text}", style="bold cyan"))

        ctx = CommandContext(
            repo_root=self.repo_root, agent=self.agent, ui=self, session=self.session
        )
        response = await dispatch(text, ctx)
        if response is not None:
            self.last_command_response = response
            style = "red" if response.startswith("ERROR") else "yellow"
            transcript.write(Text(response, style=style))
            self.refresh_status()  # /model may have swapped a slot
            return

        # One turn at a time; M3 replaces this with real barge-in/cancellation.
        event.input.disabled = True
        self.run_worker(self._agent_turn(text), exclusive=True)

    def _render_event(self, event) -> None:
        """Render one core event into the panes — agent turns and the M3
        voice bridge (via VoiceController's on_event) both land here."""
        transcript = self.query_one("#transcript", RichLog)
        if isinstance(event, SpeechChunk):
            transcript.write(Markdown(event.text))
        elif isinstance(event, ToolCallStarted):
            transcript.write(Text(f"→ {event.name}({event.args})", style="dim"))
        elif isinstance(event, Citation):
            transcript.write(Text(f"📍 {event.file}:{event.line}", style="green"))
            self.show_citation(event)
        elif isinstance(event, ScreenArtifact):
            # M0/M1 never emit these; rendered plainly until M3 refines per-kind.
            transcript.write(Markdown(event.content))

    async def _agent_turn(self, user_text: str) -> None:
        """Consume the core event stream inside a worker — the UI never blocks."""
        transcript = self.query_one("#transcript", RichLog)
        prompt = self.query_one("#prompt", Input)
        try:
            async for event in self.session.run_turn(user_text):
                self._render_event(event)
        except Exception as exc:
            # A failed turn must not kill the session (Textual workers
            # default to exit_on_error=True): show it and hand back the prompt.
            transcript.write(Text(f"ERROR: turn failed: {exc}", style="red"))
        finally:
            prompt.disabled = False
            prompt.focus()


def run_tui(repo: str) -> None:
    """Entry point for the default (TUI) channel."""
    # Imported here, not at module top: repl.py is the single agent factory
    # and importing it lazily keeps tui importable without the REPL's deps.
    from pyrrhon.repl import build_agent

    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        print(f"Not a directory: {repo_root}")
        raise SystemExit(1)
    try:
        agent = build_agent(repo_root)
    except MissingAPIKeyError as exc:
        print(exc)
        raise SystemExit(1)
    PyrrhonApp(repo_root=repo_root, agent=agent).run()
