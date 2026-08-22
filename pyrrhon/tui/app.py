"""The Textual TUI — the second channel over the headless core (M2).

Layout: one column. Transcript · status bar · input. A user who is
listening is not reading two panes, so the screen holds the conversation
and the one thing the voice is forbidden to say, the coordinates.
Agent turns run in a Textual worker so this event loop never blocks —
the same loop carries audio (spec: real-time discipline).
"""

from __future__ import annotations

from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static

from pyrrhon.bootstrap import (
    orient_in_background,
    start_channel,
    warm_index_in_background,
    warm_llm_connection_in_background,
)
from pyrrhon.channels import EventRenderer
from pyrrhon.commands import (  # noqa: F401 — registers commands
    builtin,
    debug_cmd,
    mcp_cmd,
    mode_cmd,
    plugins_cmd,
    settings_cmd,
    voice_cmd,
)
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.config.settings import load_settings
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.citation_link import citation_uri
from pyrrhon.core.events import (
    AskUser,
    Citation,
    ScreenArtifact,
    SpeechChunk,
    ToolCallStarted,
    Transcription,
    TruncateSpeech,
    VoiceNotice,
)
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import FallbackLLM
from pyrrhon.core.session import Session
from pyrrhon.tui.editor import open_in_editor
from pyrrhon.tui.theme import PYRRHON_THEME
from pyrrhon.tui.widgets import StatusBar
from pyrrhon.voice import VoiceController


def _redact_secret_echo(text: str) -> str:
    """Never render a pasted API key in the transcript (RichLog persists it).

    Masks the value in `/settings key <ENV> <secret>` before it is echoed;
    the credential itself is still stored owner-only by the command."""
    parts = text.split()
    if len(parts) >= 4 and parts[0] == "/settings" and parts[1] == "key":
        return " ".join(parts[:3]) + " ****"
    return text


class TuiRenderer(EventRenderer):
    """Core events as transcript writes.

    Composition rather than inheritance on the App: PyrrhonApp already
    subclasses Textual's App, and mixing a second base into a Textual widget
    invites metaclass surprises for no gain.
    """

    def __init__(self, app: "PyrrhonApp"):
        self._app = app

    @property
    def _transcript(self) -> RichLog:
        return self._app.query_one("#transcript", RichLog)

    def on_transcription(self, event: Transcription) -> None:
        # What STT heard — mirrors the typed "you>" so voice and text read
        # the same in the transcript. The 🎙 marks it as spoken input.
        self._transcript.write(Text(f"🎙 you> {event.text}", style="bold cyan"))

    def on_voice_notice(self, event: VoiceNotice) -> None:
        style = "bold red" if event.is_error else "yellow"
        self._transcript.write(Text(event.text, style=style))
        self._app.notify(
            event.text, severity="error" if event.is_error else "information"
        )

    def on_speech(self, event: SpeechChunk) -> None:
        self._transcript.write(Markdown(event.text))

    def on_tool_started(self, event: ToolCallStarted) -> None:
        self._transcript.write(Text(f"→ {event.name}({event.args})", style="dim"))

    def on_citation(self, event: Citation) -> None:
        # Two independent routes to the same line, so a terminal without
        # OSC 8 and a machine without $EDITOR each still have one (D2).
        uri = citation_uri(self._app.repo_root, event)
        label = f"📍 {event.file}:{event.line}"
        self._transcript.write(
            Text(label, style=f"green link {uri}" if uri else "green")
        )
        self._app.record_citation(event)

    def on_artifact(self, event: ScreenArtifact) -> None:
        # First real emitter is M14's orientation brief; rendered plainly
        # until a channel needs per-kind treatment.
        self._transcript.write(Markdown(event.content))

    def on_question(self, event: AskUser) -> None:
        # Design mode's Socratic question, rendered distinctly (spec: M6).
        self._transcript.write(Text(f"? {event.question}", style="bold magenta"))

    def on_interrupted(self, event: TruncateSpeech) -> None:
        # Voice barge-in: history was rewritten to what was actually heard.
        self._transcript.write(Text("⏹ interrupted", style="dim"))


class PyrrhonApp(App):
    TITLE = "Pyrrhon"

    # Every binding carries a description because that string is what Footer
    # renders; a binding with none is a key nothing on screen advertises.
    BINDINGS = [
        Binding("ctrl+o", "open_citation", "open citation"),
        Binding("ctrl+p", "command_palette", "commands"),
        Binding("ctrl+l", "clear_transcript", "clear"),
        Binding("f1", "help", "help"),
        # Textual maps ctrl+c to help_quit, which tells you to press ctrl+q.
        # In a terminal agent ctrl+c means stop, so it quits outright.
        Binding("ctrl+c", "quit", "quit"),
    ]

    # D6: the stylesheet is a file, not a string on the class.
    CSS_PATH = "pyrrhon.tcss"

    def __init__(
        self,
        repo_root: Path,
        agent: Agent,
        start_voice: bool = False,
        mcp: MCPManager | None = None,
        plugins: list | None = None,
    ):
        super().__init__()
        # Before anything else: CSS_PATH is parsed at startup against the
        # *current* theme's variables, so a theme registered in on_mount is
        # registered one step too late and every $token is undefined.
        self.register_theme(PYRRHON_THEME)
        self.theme = PYRRHON_THEME.name
        self.repo_root = repo_root
        self.session = Session(agent)
        self.mcp = mcp
        self.plugins = plugins or []
        self.last_citation: Citation | None = None
        # Injection point, not indirection for its own sake: the pilot has no
        # terminal to hand an editor, so the tests swap this for a recorder.
        self._open_editor = open_in_editor
        self.last_command_response: str | None = None
        self._start_voice = start_voice
        self._renderer = TuiRenderer(self)
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
        # hand them to the normal renderer (citations are recorded,
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
        yield Header()
        yield Static(id="splash")
        yield RichLog(id="transcript", wrap=True, markup=True)
        yield Input(placeholder="Ask about the repo — or /help", id="prompt")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        self.query_one("#prompt", Input).focus()
        self._show_splash()
        # Build the symbol index and open the provider connection now, so the
        # first turn pays neither the cold walk nor the TLS handshake. Held on
        # self so the tasks aren't GC'd mid-flight.
        self._warm_task = warm_index_in_background(self.agent)
        self._warm_conn_task = warm_llm_connection_in_background(self.agent)
        self._orient_task = orient_in_background(self.agent, self._render_event)
        if self._start_voice:
            self.notify(self.voice.start())

    def _show_splash(self) -> None:
        """The banner as a splash, not a logged line (defect 11).

        Written into the scrolling log it wrapped mid-glyph on a narrow
        terminal and scrolled away within one turn. As a widget it is sized
        to the terminal and cleared by the first real turn.

        Everything here is a Text object rather than a string, which is what
        fixes defect 12: a repo directory named `weird[repo]` was parsed as
        Rich markup on its way into a markup=True sink.
        """
        from pyrrhon.branding import NARROW_COLUMNS, banner, banner_narrow

        wordmark = banner() if self.size.width >= NARROW_COLUMNS else banner_narrow()
        content = Text()
        content.append_text(wordmark)
        content.append("\n\n")
        content.append(f"Discussing {self.repo_root.name}. Type /help for commands.")
        self.query_one("#splash", Static).update(content)

    def _clear_splash(self) -> None:
        """The first submitted turn takes the screen back."""
        for node in self.query("#splash"):
            node.remove()

    def record_citation(self, citation: Citation) -> None:
        """Remember the most recent citation; ctrl+o and /code both read it."""
        self.last_citation = citation

    def action_clear_transcript(self) -> None:
        """ctrl+l: clear the screen, not the session. History is untouched."""
        self.query_one("#transcript", RichLog).clear()

    async def action_help(self) -> None:
        """f1 routes through dispatch() so there is one execution path."""
        await self._run_command("/help")

    def action_open_citation(self) -> None:
        """ctrl+o: the keyboard half of D2's two routes to a cited line."""
        citation = self.last_citation
        if citation is None:
            self.notify("No citation yet — ask about the code.")
            return
        try:
            with self.suspend():
                message = self._open_editor(self.repo_root, citation)
        except SuspendNotSupported:
            # Headless drivers and Textual Web. A GUI editor never wanted the
            # terminal, so running it unsuspended beats refusing to open.
            message = self._open_editor(self.repo_root, citation)
        if message:
            self.notify(
                message, severity="error" if message.startswith("ERROR") else "warning"
            )

    def refresh_status(self) -> None:
        # Header carries identity and mode; StatusBar carries the instruments.
        self.sub_title = f"{self.repo_root.name} · {self.session.mode}"
        fast = getattr(self.agent.llm, "model", "unknown")
        deep = getattr(self.agent.deep_llm, "model", "= fast")
        self.query_one(StatusBar).show_status(
            self.session.mode, fast, deep, latency_ms=self.session.last_turn_latency_ms
        )

    @on(Input.Submitted, "#prompt")
    async def on_prompt_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        self._clear_splash()
        transcript = self.query_one("#transcript", RichLog)
        transcript.write(Text(f"you> {_redact_secret_echo(text)}", style="bold cyan"))

        if await self._run_command(text) is not None:
            return

        # One turn at a time; M3 replaces this with real barge-in/cancellation.
        event.input.disabled = True
        self.run_worker(self._agent_turn(text), exclusive=True)

    def _command_context(self) -> CommandContext:
        return CommandContext(
            repo_root=self.repo_root,
            agent=self.agent,
            ui=self,
            session=self.session,
            voice=self.voice,
            mcp=self.mcp,
            plugins=self.plugins,
        )

    async def _run_command(self, line: str) -> str | None:
        """Dispatch one slash command and render its response.

        The single execution path for every entry point: the prompt, f1, and
        the command palette. None means "not a command, send it to the agent".
        """
        response = await dispatch(line, self._command_context())
        if response is None:
            return None
        self.last_command_response = response
        style = "red" if response.startswith("ERROR") else "yellow"
        self.query_one("#transcript", RichLog).write(Text(response, style=style))
        self.refresh_status()  # /model may have swapped a slot
        return response

    def _render_event(self, event) -> None:
        """Render one core event into the panes — agent turns and the M3
        voice bridge (via VoiceController's on_event) both land here.

        Kept as a bound method because three callers pass it around as a plain
        callable (the voice bridge, the orientation task, and _agent_turn)."""
        self._renderer.render(event)

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


def run_tui(repo: str, voice: bool = False, trust_repo: bool = False) -> None:
    """Entry point for the default (TUI) channel."""

    def _ask(question: str) -> bool:
        # Textual has not taken over the terminal yet — plain input works.
        return input(f"{question} ").strip().lower() in {"y", "yes"}

    async def _serve(agent: Agent, manager: MCPManager, plugins: list) -> None:
        # run_async() rather than App.run(): start_channel already owns the
        # asyncio.run, and the MCP manager's start()/stop() must be awaited
        # from that same task (anyio cancel-scope rule).
        app = PyrrhonApp(
            repo_root=agent.repo_root,
            agent=agent,
            start_voice=voice,
            mcp=manager,
            plugins=plugins,
        )
        await app.run_async()

    start_channel(repo, _serve, ask=_ask, report=print, trust_repo=trust_repo)
