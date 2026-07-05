"""Text REPL — the first (and thinnest) channel over the headless core."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from pyrrhon.commands import builtin, debug_cmd, mcp_cmd, mode_cmd, plugins_cmd, voice_cmd  # noqa: F401 — registers commands
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.config.settings import Settings, load_settings
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.soul import build_system_prompt
from pyrrhon.core.events import AskUser, Citation, SpeechChunk, ToolCallStarted
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
from pyrrhon.core.tools.spec_writer import WriteSpecTool
from pyrrhon.core.tools.web import WebFetchTool, WebSearchTool
from pyrrhon.plugins import (
    LoadedPlugin,
    PluginManager,
    merge_plugin_settings,
    read_trusted,
    record_trusted,
)
from pyrrhon.plugins.loader import log as plugin_log


def build_agent(
    repo_root: Path,
    llm=None,
    deep_llm=None,
    extra_tools: list[Tool] | None = None,
    settings: Settings | None = None,
    home: Path | None = None,
    allow_repo_code: bool = False,
    plugins: list[LoadedPlugin] | None = None,
) -> Agent:
    settings = settings or load_settings(repo_root, home)
    if plugins is None:
        # Whoever loads the plugins merges their settings: channels that pass
        # `plugins` explicitly (REPL/TUI) pass already-merged `settings` too.
        plugins = PluginManager(repo_root, home=home).load_all(
            allow_repo_code=allow_repo_code
        )
        settings = merge_plugin_settings(settings, plugins)
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
        # Always registered: DESIGN_PROMPT instructs its use, and the
        # understand-mode prompt forbids writing spec files.
        WriteSpecTool(repo_root),
        *(extra_tools or []),  # MCP adapters join here
    ]
    existing = {tool.name for tool in tools}
    for plugin in plugins:
        for tool in plugin.tools:
            if tool.name in existing:
                plugin_log.warning(
                    "plugin %s: tool %r collides with an existing tool — ignored",
                    plugin.manifest.name, tool.name,
                )
                continue
            tools.append(tool)
            existing.add(tool.name)
    system_prompt = build_system_prompt(repo_root, home)
    plugin_prompts = "\n\n".join(p.prompt_text for p in plugins if p.prompt_text)
    if plugin_prompts:
        system_prompt += f"\n# Plugin context\n\n{plugin_prompts}\n"
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=system_prompt,
        repo_root=repo_root,
        grounding_gate=GroundingGate(repo_root),
        # REPL is a screen channel → default allow_retry=True. M3's speech
        # path constructs its Agent with allow_retry=False (spec split-path).
        deep_llm=deep_llm,
    )


def resolve_repo_code_consent(
    repo_root: Path, manager: PluginManager, ask: Callable[[str], bool]
) -> bool:
    """Once-per-repo consent gate for executing repo-level plugin code.

    Global plugins never pass through here — their code is trusted by
    installation. Returns the allow_repo_code value to hand to load_all().
    """
    pending = manager.repo_code_plugins()
    if not pending:
        return False  # nothing executable at repo level; the flag is irrelevant
    trusted = read_trusted(repo_root)
    untrusted = [name for name in pending if name not in trusted]
    if not untrusted:
        return True  # consent already on record in .pyrrhon/trusted
    names = ", ".join(untrusted)
    question = (
        f"This repo ships plugins with executable code ({names}). "
        "Run their code? [y/N]"
    )
    if ask(question):
        record_trusted(repo_root, untrusted)
        return True
    return False


def load_channel_plugins(
    repo_root: Path, ask: Callable[[str], bool]
) -> tuple[list[LoadedPlugin], Settings]:
    """Channel startup: consent gate → plugins → merged settings.

    Runs synchronously before the event loop serves turns; both channels
    (REPL and TUI) call this so the trust flow and merge order stay identical.
    """
    manager = PluginManager(repo_root)
    allow_repo_code = resolve_repo_code_consent(repo_root, manager, ask)
    plugins = manager.load_all(allow_repo_code=allow_repo_code)
    settings = merge_plugin_settings(load_settings(repo_root), plugins)
    return plugins, settings


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
    def _ask(question: str) -> bool:
        return console.input(f"[yellow]{question}[/yellow] ").strip().lower() in {"y", "yes"}

    # Consent + plugin loading run before the event loop exists: the merged
    # settings decide which MCP servers _repl_main starts.
    plugins, settings = load_channel_plugins(repo_root, _ask)
    try:
        # Agent construction happens inside the loop: the MCP manager's
        # start()/stop() must run in the same asyncio task (anyio rule).
        asyncio.run(_repl_main(console, repo_root, plugins, settings))
    except MissingAPIKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
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
        console.print(
            f"[bold]Pyrrhon[/bold] — discussing [cyan]{repo_root.name}[/cyan]. "
            "Commands: /help, /quit"
        )
        if plugins:
            loaded = ", ".join(f"{p.manifest.name}@{p.manifest.version}" for p in plugins)
            console.print(f"[dim]plugins: {loaded}[/dim]")
        await _repl_loop(agent, console, repo_root, mcp=manager, plugins=plugins)
    finally:
        await manager.stop()  # same task as start() — anyio cancel-scope rule


async def _repl_loop(
    agent: Agent,
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
            console.print(f"[green]📍 {event.file}:{event.line}[/green]")
        elif isinstance(event, AskUser):
            # Design mode's Socratic question, rendered distinctly (spec: M6).
            console.print(f"[bold magenta]? {event.question}[/bold magenta]")
