"""The Textual TUI — the second channel over the headless core (M2).

Layout: transcript (left) · code viewer (right) · status bar · input.
Agent turns run in a Textual worker so this event loop never blocks —
from M3 the same loop carries audio (spec: real-time discipline).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, RichLog

from pyrrhon.commands import builtin, debug_cmd, mcp_cmd, voice_cmd  # noqa: F401 — registers commands
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.config.settings import load_settings
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import (
    Citation,
    ScreenArtifact,
    SpeechChunk,
    ToolCallStarted,
    TruncateSpeech,
)
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import FallbackLLM, MissingAPIKeyError
from pyrrhon.core.session import Session
from pyrrhon.tui.widgets import CodeViewer, StatusBar
from pyrrhon.voice import VoiceController


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

    def __init__(
        self,
        repo_root: Path,
        agent: Agent,
        start_voice: bool = False,
        mcp: MCPManager | None = None,
    ):
        super().__init__()
        self.repo_root = repo_root
        self.session = Session(agent)
        self.mcp = mcp
        self.last_citation: Citation | None = None
        self.last_command_response: str | None = None
        self._start_voice = start_voice
        if isinstance(agent.llm, FallbackLLM):
            llm = agent.llm
            # Spec: provider failure -> one-sentence notice. on_switch fires
            # inside a worker on this app's loop; call_later hands the toast
            # to Textual's own scheduling.
            llm.on_switch = lambda i: self.call_later(
                self.notify,
                f"My primary model stopped responding — switching to {llm.chain[i].model}.",
            )
        self.voice = VoiceController(
            self.session,
            load_settings(repo_root),
            on_event=self._on_voice_event,
            notify=self._notify_voice,
        )

    def _on_voice_event(self, event) -> None:
        # Bridge events arrive on the same asyncio loop Textual runs on;
        # hand them to the normal renderer (citations jump the code viewer,
        # TruncateSpeech marks the transcript).
        self.call_later(self._render_event, event)

    def _notify_voice(self, message: str) -> None:
        self.call_later(self.notify, message)

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
        if self._start_voice:
            self.notify(self.voice.start())

    def show_citation(self, citation: Citation) -> None:
        """Record the citation and jump the code viewer to it."""
        self.last_citation = citation
        self.query_one(CodeViewer).show(citation, self.repo_root)

    def refresh_status(self) -> None:
        fast = getattr(self.agent.llm, "model", "unknown")
        deep = getattr(getattr(self.agent, "deep_llm", None), "model", "= fast")
        self.query_one(StatusBar).show_status(
            "understand", fast, deep, latency_ms=self.session.last_turn_latency_ms
        )

    @on(Input.Submitted, "#prompt")
    async def on_prompt_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        transcript = self.query_one("#transcript", RichLog)
        transcript.write(Text(f"you> {text}", style="bold cyan"))

        ctx = CommandContext(
            repo_root=self.repo_root,
            agent=self.agent,
            ui=self,
            session=self.session,
            voice=self.voice,
            mcp=self.mcp,
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
        elif isinstance(event, TruncateSpeech):
            # Voice barge-in: history was rewritten to what was actually heard.
            transcript.write(Text("⏹ interrupted", style="dim"))

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
            self.refresh_status()  # picks up the turn's latency measurement
            prompt.disabled = False
            prompt.focus()


def run_tui(repo: str, voice: bool = False) -> None:
    """Entry point for the default (TUI) channel."""
    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        print(f"Not a directory: {repo_root}")
        raise SystemExit(1)
    try:
        # One asyncio.run for MCP lifecycle + Textual: the manager's start()
        # and stop() must be awaited from the same task (anyio rule), so the
        # app runs via run_async() inside that task instead of App.run().
        asyncio.run(_tui_main(repo_root, voice))
    except MissingAPIKeyError as exc:
        print(exc)
        raise SystemExit(1)


async def _tui_main(repo_root: Path, voice: bool) -> None:
    # Imported here, not at module top: repl.py is the single agent factory
    # and importing it lazily keeps tui importable without the REPL's deps.
    from pyrrhon.repl import build_agent

    settings = load_settings(repo_root)
    manager = MCPManager(settings.mcp_servers)
    mcp_tools = await manager.start()  # never raises; dead servers log one warning
    try:
        agent = build_agent(repo_root, extra_tools=mcp_tools)
        app = PyrrhonApp(
            repo_root=repo_root, agent=agent, start_voice=voice, mcp=manager
        )
        await app.run_async()
    finally:
        await manager.stop()
