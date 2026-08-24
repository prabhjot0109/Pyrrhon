"""The Textual TUI — the second channel over the headless core (M2).

Layout: one column. Transcript · status bar · input. A user who is
listening is not reading two panes, so the screen holds the conversation
and the one thing the voice is forbidden to say, the coordinates.
Agent turns run in a Textual worker so this event loop never blocks —
the same loop carries audio (spec: real-time discipline).
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Static, TextArea

from pyrrhon.bootstrap import (
    orient_in_background,
    start_channel,
    warm_index_in_background,
    warm_llm_connection_in_background,
)
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
from pyrrhon.core.context import history_tokens
from pyrrhon.core.events import Citation
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import FallbackLLM
from pyrrhon.core.session import Session
from pyrrhon.tui import status
from pyrrhon.tui.completion import CommandMenu, matches
from pyrrhon.tui.editor import open_in_editor
from pyrrhon.tui.messages import CommandRow, InterruptRow, NoticeRow, UserRow
from pyrrhon.tui.palette import PyrrhonCommands
from pyrrhon.tui.prompt import Prompt
from pyrrhon.tui.renderer import TuiRenderer
from pyrrhon.tui.splash import splash_text
from pyrrhon.tui.status import StatusBar
from pyrrhon.tui.theme import PYRRHON_THEME, TOKENS
from pyrrhon.tui.turn import TurnView
from pyrrhon.voice import VoiceController


def _redact_secret_echo(text: str) -> str:
    """Never render a pasted API key in the transcript (RichLog persists it).

    Masks the value in `/settings key <ENV> <secret>` before it is echoed;
    the credential itself is still stored owner-only by the command."""
    parts = text.split()
    if len(parts) >= 4 and parts[0] == "/settings" and parts[1] == "key":
        return " ".join(parts[:3]) + " ****"
    return text


class PyrrhonApp(App):
    TITLE = "Pyrrhon"

    # Composed with Textual's own providers rather than replacing them (D4).
    COMMANDS = App.COMMANDS | {PyrrhonCommands}

    # Every binding carries a description because that string is what Footer
    # renders; a binding with none is a key nothing on screen advertises.
    #
    # ctrl+p is deliberately absent. Textual binds the command palette itself
    # and the Footer advertises it from that binding, so declaring a second
    # one printed "^p commands" twice on the same line.
    BINDINGS = [
        # priority, because the prompt has focus for the whole session and
        # would otherwise swallow escape before the app ever sees it.
        Binding("escape", "abort_turn", "stop", priority=True),
        Binding("ctrl+o", "open_citation", "open citation"),
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
        # Everything a turn puts on screen, and nothing that outlives it.
        self.turn = TurnView(self)
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

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Pyrrhon's own tokens, under whatever theme is active.

        Textual builds $variables from the current theme alone, so tokens that
        live only in our Theme vanish the moment the user picks another one
        from the command palette — and the stylesheet then fails to parse with
        "reference to undefined variable '$evidence'". This is the documented
        hook for variables that must outlive a theme switch.
        """
        return dict(TOKENS)

    def compose(self) -> ComposeResult:
        # No Header. It spent a row restating the title bar and the repo name,
        # and the repo name is a piece of state, so it belongs on the one line
        # that already carries state.
        #
        # D3: a VerticalScroll of mounted widgets, not a RichLog. A mounted
        # row can be updated after the fact, which is the one structural
        # reason a spinner, an elapsed timer and a resolving tool row are
        # possible at all.
        yield VerticalScroll(id="transcript")
        yield CommandMenu(id="completion")
        yield Prompt(placeholder="Ask about the repo — or /help", id="prompt")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        prompt = self.query_one("#prompt", Prompt)
        menu = self.query_one("#completion", CommandMenu)
        menu.display = False
        prompt.completion = menu
        prompt.focus()
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
        """The banner, mounted *inside* the transcript.

        As a sibling of the transcript it owned a fixed slice of the column,
        so removing it resized the scroll view under a scroll offset computed
        against the old geometry — the conversation jumped. Inside, it is an
        ordinary row: it scrolls, and clearing it costs the transcript nothing.
        """
        self.query_one("#transcript", VerticalScroll).mount(
            Static(splash_text(self.repo_root, self.size.width), id="splash")
        )

    def _clear_splash(self) -> None:
        """The first submitted turn takes the screen back."""
        for node in self.query("#splash"):
            node.remove()

    def mount_row(self, row, before=None) -> None:
        """Mount a transcript row, keeping the newest content in view.

        Not `VerticalScroll.anchor()`, which is what this replaces. Its
        scroll_end runs `immediate=True`, i.e. before the layout that would
        tell it how far it may scroll, and the offset it settles on is never
        revised: the transcript sat at scroll_y = -(viewport height) for the
        whole session. Every row still rendered, but bottom-aligned under a
        screen-high blank gap — the "enormous gap under the question" that had
        already been chased through MarkdownStream and a `1fr` rail, and whose
        last cause was this.

        Following explicitly keeps what the anchor was for. A user who has
        scrolled up is left where they are, and the check runs *before* the
        mount because mounting is what moves max_scroll_y underneath it.
        """
        transcript = self.query_one("#transcript", VerticalScroll)
        following = transcript.scroll_offset.y >= transcript.max_scroll_y
        if before is None:
            transcript.mount(row)
        else:
            transcript.mount(row, before=before)
        if following:
            # After the refresh, so max_scroll_y accounts for the new row.
            transcript.call_after_refresh(transcript.scroll_end, animate=False)

    def record_citation(self, citation: Citation) -> None:
        """Remember the most recent citation; ctrl+o and /code both read it."""
        self.last_citation = citation

    def request_exit(self) -> None:
        """`/exit` and `/quit`, from the one command table both channels read.

        Deferred rather than immediate: dispatch() is still mid-flight and its
        caller mounts the response afterwards, so tearing the app down here
        would render "Goodbye." into a screen that no longer exists.
        """
        self.call_later(self.exit)

    def action_clear_transcript(self) -> None:
        """ctrl+l: clear the screen, not the session. History is untouched."""
        self.query_one("#transcript", VerticalScroll).remove_children()

    async def action_help(self) -> None:
        """f1 routes through dispatch() so there is one execution path."""
        await self.run_command("/help")

    def action_abort_turn(self) -> None:
        """esc, in precedence order: close the menu, clear the prompt, stop
        the turn. D5, and the second caller of a path voice barge-in already
        exercises on every interruption.

        The order is the point. esc means "undo the thing I just started", and
        the most recent thing is always the innermost one — so an open command
        menu closes without touching what was typed, and only a prompt with
        nothing left to dismiss reaches the turn. The key is never dead at any
        step.
        """
        menu = self.query_one("#completion", CommandMenu)
        if menu.display:
            menu.hide()
            return
        prompt = self.query_one("#prompt", Prompt)
        if not prompt.disabled:
            prompt.clear()
            return
        self.session.abort_current_turn()
        self.mount_row(InterruptRow())

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
        # The status line carries every piece of state, now including the repo
        # name that used to sit in a Header of its own. sub_title still gets
        # set, because that is what names the terminal window.
        self.sub_title = self.repo_root.name
        status.sync(
            self.query_one(StatusBar),
            repo=self.repo_root.name,
            mode=self.session.mode,
            fast_model=getattr(self.agent.llm, "model", "unknown"),
            deep_model=getattr(self.agent.deep_llm, "model", ""),
            latency_ms=self.session.last_turn_latency_ms,
            # Measured since M15b and displayed by nobody until now (defect 8).
            # token_scale is the calibration from what the provider actually
            # charged, so this tracks the real window rather than len//4.
            context_used=history_tokens(self.session.history, self.agent.token_scale),
            context_budget=getattr(self.agent, "context_budget_tokens", 0),
            voice_state=self.voice_state(),
        )

    def voice_state(self) -> str:
        """off / listening / speaking, derived rather than eventful.

        The bridge already emits everything needed to tell these apart, so this
        reads what the channel knows instead of adding a core event for the
        screen's benefit (defect 9).
        """
        if not self.voice.running:
            return "off"
        return "speaking" if self.turn.speaking else "listening"

    def refresh_voice_state(self) -> None:
        """The one field the working row's timer repaints."""
        self.query_one(StatusBar).voice_state = self.voice_state()

    @on(TextArea.Changed, "#prompt")
    def on_prompt_changed(self, event: TextArea.Changed) -> None:
        """Offer commands as the name is typed, and stop once it is a sentence."""
        self.query_one("#completion", CommandMenu).show(matches(event.text_area.text))

    @on(Prompt.Submitted, "#prompt")
    async def on_prompt_submitted(self, event: Prompt.Submitted) -> None:
        text = event.value.strip()
        event.prompt.clear()
        if not text:
            return
        self._clear_splash()
        self.query_one("#completion", CommandMenu).hide()
        self.mount_row(UserRow(_redact_secret_echo(text)))

        if await self.run_command(text) is not None:
            return

        # One turn at a time; M3 replaces this with real barge-in/cancellation.
        event.prompt.disabled = True
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

    async def run_command(self, line: str) -> str | None:
        """Dispatch one slash command and render its response.

        The single execution path for every entry point: the prompt, f1, and
        the command palette. None means "not a command, send it to the agent".
        """
        response = await dispatch(line, self._command_context())
        if response is None:
            return None
        self.last_command_response = response
        failed = response.startswith("ERROR")
        # A command's answer is machinery, not a claim about the code. It went
        # out as a NoticeRow, which meant `/help` arrived under the ⚠ rail in
        # hedge amber — the styling that means "Pyrrhon could not verify this".
        self.mount_row(NoticeRow(response, is_error=True) if failed else CommandRow(response))
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
        prompt = self.query_one("#prompt", Prompt)
        await self.turn.start()
        try:
            async for event in self.session.run_turn(user_text):
                self._render_event(event)
        except Exception as exc:
            # A failed turn must not kill the session (Textual workers
            # default to exit_on_error=True): show it and hand back the prompt.
            self.mount_row(NoticeRow(f"ERROR: turn failed: {exc}", is_error=True))
        finally:
            # The stream is closed here and nowhere else, so an aborted or
            # failed turn cannot leak one into the next turn's row.
            # CancelledError is a BaseException, so the broad `except
            # Exception` above cannot swallow an abort; it lands here, which
            # is what makes esc leave the prompt usable.
            await self.turn.finish()
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
