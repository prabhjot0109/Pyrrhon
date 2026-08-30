"""Text REPL — the first (and thinnest) channel over the headless core.

Startup (trust gate, agent construction, MCP lifecycle, warm-ups) lives in
`pyrrhon.bootstrap`; event dispatch lives in `pyrrhon.channels`. What is left
here is only what makes this channel a channel: reading a line, and choosing
how each event looks as terminal output.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

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
from pyrrhon.core.citation_link import citation_markup
from pyrrhon.core.events import (
    AskUser,
    Citation,
    ProviderRetrying,
    ScreenArtifact,
    SpeechChunk,
    ToolCallStarted,
)
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import FallbackLLM
from pyrrhon.core.session import Session
from pyrrhon.plugins import LoadedPlugin

log = logging.getLogger("pyrrhon.repl")


class ConsoleUI:
    """Duck-typed `ui` for CommandContext in the text channel."""

    def __init__(self, console: Console):
        self._console = console
        self.last_citation: Citation | None = None
        # Set by /exit and /quit, read once per iteration of the read loop.
        # A command handler returns a string and never raises, so leaving is
        # a flag rather than an exception.
        self.exiting = False

    def request_exit(self) -> None:
        self.exiting = True

    def notify(self, text: str) -> None:
        self._console.print(text)


class ConsoleRenderer(EventRenderer):
    """Core events as terminal output.

    Hooks left at their no-op defaults are the deliberate omissions: this
    channel has no code pane, no microphone, and nothing useful to say about a
    tool *finishing* — the started line already told the user what is running.
    """

    def __init__(self, console: Console, ui: ConsoleUI, repo_root: Path):
        self._console = console
        self._ui = ui
        self._repo_root = repo_root

    def on_tool_started(self, event: ToolCallStarted) -> None:
        self._console.print(f"[dim]→ {event.name}({event.args})[/dim]")

    def on_speech(self, event: SpeechChunk) -> None:
        self._console.print(Markdown(event.text))

    def on_citation(self, event: Citation) -> None:
        self._ui.last_citation = event  # /code opens the most recent citation
        self._console.print(citation_markup(self._repo_root, event))

    def on_artifact(self, event: ScreenArtifact) -> None:
        self._console.print(Markdown(event.content))

    def on_question(self, event: AskUser) -> None:
        # Design mode's Socratic question, rendered distinctly (spec: M6).
        self._console.print(f"[bold magenta]? {event.question}[/bold magenta]")

    def on_provider_retrying(self, event: ProviderRetrying) -> None:
        self._console.print(
            f"[yellow]Rate limited — retrying in "
            f"{event.delay_seconds:.0f}s.[/yellow]"
        )


def run_repl(repo: str, voice: bool = False, trust_repo: bool = False) -> None:
    console = Console()
    if voice:
        # Voice is a TUI-channel feature; the plain REPL stays text-only.
        console.print(
            "[yellow]--voice needs the TUI — run plain `pyrrhon` for voice; "
            "continuing in text mode.[/yellow]"
        )

    def _ask(question: str) -> bool:
        return console.input(f"[yellow]{question}[/yellow] ").strip().lower() in {"y", "yes"}

    async def _serve(agent, manager: MCPManager, plugins: list[LoadedPlugin]) -> None:
        # Both warm-ups overlap startup and the user's first utterance. Refs
        # held so neither task is garbage-collected mid-flight.
        warm = warm_index_in_background(agent)  # noqa: F841
        warm_conn = warm_llm_connection_in_background(agent)  # noqa: F841
        from pyrrhon.branding import banner

        console.print(banner())  # pre-styled Text; an outer style would flatten it
        console.print(
            f"Discussing [cyan]{agent.repo_root.name}[/cyan]. Commands: /help, /exit"
        )
        if plugins:
            names = ", ".join(
                f"{p.manifest.name}@{p.manifest.version}" for p in plugins
            )
            console.print(f"[dim]plugins: {names}[/dim]")
        # After the banner, before the first prompt is waited on. Ref held so
        # the task isn't garbage-collected mid-build.
        orient = orient_in_background(  # noqa: F841
            agent, lambda brief: console.print(Markdown(brief.content))
        )
        await _repl_loop(
            agent, console, agent.repo_root, mcp=manager, plugins=plugins
        )

    start_channel(
        repo,
        _serve,
        ask=_ask,
        report=lambda msg: console.print(f"[red]{msg}[/red]"),
        trust_repo=trust_repo,
    )


async def _repl_loop(
    agent,
    console: Console,
    repo_root: Path,
    mcp: MCPManager | None = None,
    plugins: list[LoadedPlugin] | None = None,
) -> None:
    ui = ConsoleUI(console)
    renderer = ConsoleRenderer(console, ui, agent.repo_root)
    if isinstance(agent.llm, FallbackLLM):
        llm = agent.llm
        # Spec: provider failure -> fallback chain "with a one-sentence
        # spoken notice". In this text channel the notice prints.
        llm.on_switch = lambda i: ui.notify(
            f"My primary model stopped responding — switching to {llm.chain[i].model}."
        )
    # Same attachment shape as on_switch, but the payload is a core event, so
    # the dispatch table decides how each channel says it rather than each
    # channel inventing a second notify format. Set on whatever driver is
    # configured, chain or not; a test double simply grows an unread attribute.
    agent.llm.on_retry = lambda delay, reason: renderer.render(
        ProviderRetrying(delay_seconds=delay, reason=reason)
    )
    session = Session(agent)
    # voice stays None: the plain REPL is a text channel; /voice answers honestly.
    ctx = CommandContext(
        repo_root=repo_root,
        agent=agent,
        ui=ui,
        session=session,
        mcp=mcp,
        plugins=plugins or [],
    )
    while True:
        try:
            user = (await asyncio.to_thread(console.input, "[bold cyan]you> [/bold cyan]")).strip()
        except EOFError:
            break
        if not user:
            continue
        response = await dispatch(user, ctx)
        if response is not None:
            console.print(response)
            # /exit and /quit are rows in the command table now, not names
            # this loop matches before dispatch — which is what puts them in
            # /help and in the TUI's menu.
            if ui.exiting:
                break
            continue
        await _turn(session, user, renderer)
        if session.last_turn_latency_ms is not None:
            console.print(
                f"[dim](first response in {session.last_turn_latency_ms:.0f} ms)[/dim]"
            )


async def _turn(session: Session, user: str, renderer: ConsoleRenderer) -> None:
    """One REPL turn, rendered. A seam rather than an inline loop because
    tests/test_channels_session.py drives it directly to check that a turn
    reaches the console at all."""
    async for event in session.run_turn(user):
        renderer.render(event)
