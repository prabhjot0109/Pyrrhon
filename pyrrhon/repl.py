"""Text REPL — the first (and thinnest) channel over the headless core."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from pyrrhon.commands import builtin, debug_cmd, mcp_cmd, voice_cmd  # noqa: F401 — registers commands
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.config.settings import load_settings
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.soul import build_system_prompt
from pyrrhon.core.events import Citation, SpeechChunk, ToolCallStarted
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import (
    FallbackLLM,
    MissingAPIKeyError,
    create_llm_with_fallbacks,
)
from pyrrhon.core.session import Session
from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.ast_index import FindReferencesTool, FindSymbolTool, SymbolIndex
from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool
from pyrrhon.core.tools.memory import RememberTool
from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool
from pyrrhon.core.tools.web import WebFetchTool, WebSearchTool


def build_agent(
    repo_root: Path,
    llm=None,
    deep_llm=None,
    extra_tools: list[Tool] | None = None,
) -> Agent:
    settings = load_settings(repo_root)
    # With no [fallbacks] configured this returns exactly what create_llm did.
    llm = llm or create_llm_with_fallbacks("fast", settings)
    if deep_llm is None:
        try:
            # deep_slot falls back to fast when [deep] is unset (Settings rule).
            deep_llm = create_llm_with_fallbacks("deep", settings)
        except MissingAPIKeyError:
            deep_llm = None  # no key for the deep slot -> think_deeper not registered
    index = SymbolIndex(repo_root)
    tools = [
        ReadFileTool(repo_root),
        GrepTool(repo_root),
        GlobTool(repo_root),
        RememberTool(repo_root),
        FindSymbolTool(index),
        FindReferencesTool(index),
        GitLogTool(repo_root),
        GitBlameTool(repo_root),
        GitShowTool(repo_root),
        WebSearchTool(),
        WebFetchTool(),
        *(extra_tools or []),  # MCP adapters join here
    ]
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=build_system_prompt(repo_root),
        repo_root=repo_root,
        grounding_gate=GroundingGate(repo_root),
        # REPL is a screen channel → default allow_retry=True. M3's speech
        # path constructs its Agent with allow_retry=False (spec split-path).
        deep_llm=deep_llm,
    )


class ConsoleUI:
    """Duck-typed `ui` for CommandContext in the text channel."""

    def __init__(self, console: Console):
        self._console = console
        self.last_citation: Citation | None = None

    def notify(self, text: str) -> None:
        self._console.print(text)


def run_repl(repo: str, voice: bool = False) -> None:
    console = Console()
    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        console.print(f"[red]Not a directory: {repo_root}[/red]")
        raise SystemExit(1)
    if voice:
        # Voice is a TUI-channel feature; the plain REPL stays text-only.
        console.print(
            "[yellow]--voice needs the TUI — run plain `pyrrhon` for voice; "
            "continuing in text mode.[/yellow]"
        )
    try:
        # Agent construction happens inside the loop: the MCP manager's
        # start()/stop() must run in the same asyncio task (anyio rule).
        asyncio.run(_repl_main(console, repo_root))
    except MissingAPIKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass


async def _repl_main(console: Console, repo_root: Path) -> None:
    settings = load_settings(repo_root)
    manager = MCPManager(settings.mcp_servers)
    mcp_tools = await manager.start()  # never raises; dead servers log one warning
    try:
        agent = build_agent(repo_root, extra_tools=mcp_tools)
        console.print(
            f"[bold]Pyrrhon[/bold] — discussing [cyan]{repo_root.name}[/cyan]. "
            "Commands: /help, /quit"
        )
        await _repl_loop(agent, console, repo_root, mcp=manager)
    finally:
        await manager.stop()  # same task as start() — anyio cancel-scope rule


async def _repl_loop(
    agent: Agent, console: Console, repo_root: Path, mcp: MCPManager | None = None
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
        repo_root=repo_root, agent=agent, ui=ui, session=session, mcp=mcp
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


async def _turn(session: Session, user: str, console: Console, ui: ConsoleUI) -> None:
    async for event in session.run_turn(user):
        if isinstance(event, ToolCallStarted):
            console.print(f"[dim]→ {event.name}({event.args})[/dim]")
        elif isinstance(event, SpeechChunk):
            console.print(Markdown(event.text))
        elif isinstance(event, Citation):
            ui.last_citation = event  # /code opens the most recent citation
            console.print(f"[green]📍 {event.file}:{event.line}[/green]")
