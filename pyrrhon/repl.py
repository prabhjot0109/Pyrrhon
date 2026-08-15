"""Text REPL — the first (and thinnest) channel over the headless core.

Startup (trust gate, agent construction, warm-ups) lives in
`pyrrhon.bootstrap`, which every channel shares. What is left here is only
what makes this channel a channel: reading a line, and rendering events.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from pyrrhon.bootstrap import (
    build_agent,
    load_channel_plugins,
    orient_in_background,
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
from pyrrhon.config.settings import Settings
from pyrrhon.core.citation_link import citation_markup
from pyrrhon.core.events import AskUser, Citation, SpeechChunk, ToolCallStarted
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import FallbackLLM, MissingAPIKeyError
from pyrrhon.core.session import Session
from pyrrhon.plugins import LoadedPlugin

log = logging.getLogger("pyrrhon.repl")


class ConsoleUI:
    """Duck-typed `ui` for CommandContext in the text channel."""

    def __init__(self, console: Console):
        self._console = console
        self.last_citation: Citation | None = None

    def notify(self, text: str) -> None:
        self._console.print(text)


def run_repl(repo: str, voice: bool = False, trust_repo: bool = False) -> None:
    console = Console()
    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        console.print(f"[red]Not a directory: {repo_root}[/red]")
        raise SystemExit(1)

    from pyrrhon.config.wizard import ensure_configured

    ensure_configured()  # stored keys → env; first run offers the wizard
    if voice:
        # Voice is a TUI-channel feature; the plain REPL stays text-only.
        console.print(
            "[yellow]--voice needs the TUI — run plain `pyrrhon` for voice; "
            "continuing in text mode.[/yellow]"
        )
    def _ask(question: str) -> bool:
        return console.input(f"[yellow]{question}[/yellow] ").strip().lower() in {"y", "yes"}

    # Consent + plugin loading run before the event loop exists: the merged
    # settings decide which MCP servers _repl_main starts. isatty() decides
    # whether prompting is even possible — piped stdin must refuse, not hang.
    plugins, settings = load_channel_plugins(
        repo_root, _ask, trust_repo=trust_repo, interactive=sys.stdin.isatty()
    )
    try:
        # Agent construction happens inside the loop: the MCP manager's
        # start()/stop() must run in the same asyncio task (anyio rule).
        asyncio.run(_repl_main(console, repo_root, plugins, settings))
    except MissingAPIKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        # `from exc`: the message is already printed, but chaining keeps the
        # cause honest for anyone who runs this under a debugger.
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        pass


async def _repl_main(
    console: Console,
    repo_root: Path,
    plugins: list[LoadedPlugin],
    settings: Settings,
) -> None:
    manager = MCPManager(settings.mcp_servers)
    mcp_tools = await manager.start()  # never raises; dead servers log one warning
    try:
        agent = build_agent(
            repo_root, extra_tools=mcp_tools, settings=settings, plugins=plugins
        )
        # Both warm-ups overlap startup and the user's first utterance. Refs
        # held so neither task is garbage-collected mid-flight.
        warm = warm_index_in_background(agent)  # noqa: F841
        warm_conn = warm_llm_connection_in_background(agent)  # noqa: F841
        from pyrrhon.branding import banner

        console.print(banner())  # pre-styled Text; an outer style would flatten it
        console.print(
            f"Discussing [cyan]{repo_root.name}[/cyan]. Commands: /help, /quit"
        )
        if plugins:
            loaded = ", ".join(f"{p.manifest.name}@{p.manifest.version}" for p in plugins)
            console.print(f"[dim]plugins: {loaded}[/dim]")
        # After the banner, before the first prompt is waited on. Ref held so
        # the task isn't garbage-collected mid-build.
        orient = orient_in_background(  # noqa: F841
            agent, lambda brief: console.print(Markdown(brief.content))
        )
        await _repl_loop(agent, console, repo_root, mcp=manager, plugins=plugins)
    finally:
        await manager.stop()  # same task as start() — anyio cancel-scope rule


async def _repl_loop(
    agent,
    console: Console,
    repo_root: Path,
    mcp: MCPManager | None = None,
    plugins: list[LoadedPlugin] | None = None,
) -> None:
    ui = ConsoleUI(console)
    if isinstance(agent.llm, FallbackLLM):
        llm = agent.llm
        # Spec: provider failure -> fallback chain "with a one-sentence
        # spoken notice". In this text channel the notice prints.
        llm.on_switch = lambda i: ui.notify(
            f"My primary model stopped responding — switching to {llm.chain[i].model}."
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
        if user in {"/quit", "/exit"}:
            break
        response = await dispatch(user, ctx)
        if response is not None:
            console.print(response)
            continue
        await _turn(session, user, console, ui)
        if session.last_turn_latency_ms is not None:
            console.print(
                f"[dim](first response in {session.last_turn_latency_ms:.0f} ms)[/dim]"
            )


async def _turn(session: Session, user: str, console: Console, ui: ConsoleUI) -> None:
    async for event in session.run_turn(user):
        if isinstance(event, ToolCallStarted):
            console.print(f"[dim]→ {event.name}({event.args})[/dim]")
        elif isinstance(event, SpeechChunk):
            console.print(Markdown(event.text))
        elif isinstance(event, Citation):
            ui.last_citation = event  # /code opens the most recent citation
            console.print(citation_markup(session.agent.repo_root, event))
        elif isinstance(event, AskUser):
            # Design mode's Socratic question, rendered distinctly (spec: M6).
            console.print(f"[bold magenta]? {event.question}[/bold magenta]")
