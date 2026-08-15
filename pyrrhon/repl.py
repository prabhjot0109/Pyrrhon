"""Text REPL — the first (and thinnest) channel over the headless core."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

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
from pyrrhon.config.settings import Settings, load_settings
from pyrrhon.config.trust import Grant, read_trust_file, record_grants
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.soul import build_system_prompt, pending_soul_grants
from pyrrhon.core.citation_link import citation_markup
from pyrrhon.core.events import (
    AskUser,
    Citation,
    ScreenArtifact,
    SpeechChunk,
    ToolCallStarted,
)
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import (
    FallbackLLM,
    MissingAPIKeyError,
    create_llm_with_fallbacks,
)
from pyrrhon.core.session import Session
from pyrrhon.core.tools.ast_index import (
    DependenciesTool,
    FindSymbolTool,
    RepoMapTool,
    SymbolIndex,
)
from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool
from pyrrhon.core.tools.memory import RememberTool
from pyrrhon.core.tools.orientation import build_orientation
from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool
from pyrrhon.core.tools.spec_writer import WriteSpecTool
from pyrrhon.core.tools.symbol_context import SymbolContextTool
from pyrrhon.core.tools.web import WebFetchTool, WebSearchTool
from pyrrhon.plugins import (
    LoadedPlugin,
    PluginManager,
    merge_plugin_settings,
    record_trusted,
)
from pyrrhon.plugins.loader import log as plugin_log

log = logging.getLogger("pyrrhon.repl")


def warm_index_in_background(agent: Agent) -> asyncio.Task | None:
    """Kick off the symbol index's cold build so the first index-using turn
    doesn't pay for it (it overlaps startup / the user's first utterance).

    Fire-and-forget: any failure is swallowed because the index rebuilds
    lazily on first real use regardless. Returns the task so the caller can
    hold a reference (keeping it from being garbage-collected mid-build)."""
    tool = agent.tools.get("find_symbol")
    index = getattr(tool, "index", None)
    if index is None:
        return None

    async def _warm() -> None:
        try:
            await index.ensure_fresh()
        except Exception:  # pragma: no cover - defensive; lazy build still works
            log.debug("index warm-up failed; will build lazily", exc_info=True)

    return asyncio.create_task(_warm())


def orient_in_background(
    agent: Agent, render: Callable[[ScreenArtifact], object]
) -> asyncio.Task | None:
    """Render the orientation brief once the index is warm, off the startup path.

    Never awaited by the caller: a brief is a nicety, and the first prompt must
    not wait on a cold index walk to appear. Same fire-and-forget shape as the
    warm-ups above, and for the same reason — a repo with no readable source is
    a normal case, not a startup failure.
    """
    tool = agent.tools.get("find_symbol")
    index = getattr(tool, "index", None)
    if index is None:
        return None

    async def _orient() -> None:
        try:
            render(await build_orientation(agent.repo_root, index))
        except Exception:  # pragma: no cover - never let a brief break startup
            log.debug("orientation brief failed", exc_info=True)

    return asyncio.create_task(_orient())


def _active_openai_client(llm):
    """The AsyncOpenAI instance behind the agent's LLM, or None.

    Returns None for test doubles and for anything that isn't an
    OpenAICompatLLM, which is what makes the warm-up a no-op under test.
    """
    chain = getattr(llm, "chain", None)  # FallbackLLM wraps a list
    if chain:
        llm = chain[0]
    return getattr(llm, "_client", None)


def warm_llm_connection_in_background(agent: Agent) -> asyncio.Task | None:
    """Open the provider's HTTP connection before the first turn needs it.

    The index warm-up above removes the cold-index cost from turn one; this
    removes the other one. The first llm.chat() of a session pays DNS + TCP +
    TLS handshake on top of model latency — easily a few hundred milliseconds,
    and it lands on the turn that forms the user's first impression.
    AsyncOpenAI holds an httpx connection pool, so any completed round trip
    amortizes the handshake to zero for every turn after it.

    models.list() is the probe because it is cheap, public, and supported by
    essentially every OpenAI-compatible endpoint including local ones. Even a
    401 warms the pool — the handshake happens before the status code — which
    is why every failure is swallowed rather than surfaced.
    """
    client = _active_openai_client(agent.llm)
    if client is None:
        return None

    async def _warm() -> None:
        try:
            await client.models.list()
        except Exception:
            log.debug(
                "llm connection warm-up failed; turn one pays the handshake",
                exc_info=True,
            )

    return asyncio.create_task(_warm())


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
        # symbol_context replaces find_references (M14): same `name` argument,
        # same rows from the same read-only index, plus the source window and
        # import edges — one round trip where "how does X work" took three.
        # list_dependencies STAYS: it is path-addressed, and "what imports
        # loop.py?" carries no symbol to hang a name-addressed query on.
        SymbolContextTool(index, repo_root),
        DependenciesTool(index),
        RepoMapTool(index),
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
    # The deep subagent's read-only belt. Excluded on purpose: think_deeper
    # (recursion), write_spec (read-only), remember (fast model owns memory),
    # web tools (repo questions stay in the repo), MCP/plugin tools
    # (uncontrolled cost).
    deep_tools = [
        ReadFileTool(repo_root),
        GrepTool(repo_root),
        GlobTool(repo_root),
        FindSymbolTool(index),
        SymbolContextTool(index, repo_root),
        DependenciesTool(index),
        RepoMapTool(index),
        GitLogTool(repo_root),
        GitBlameTool(repo_root),
        GitShowTool(repo_root),
    ]
    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt=system_prompt,
        repo_root=repo_root,
        grounding_gate=GroundingGate(
            repo_root, require_provenance=settings.grounding.require_provenance
        ),
        # REPL is a screen channel → default allow_retry=True. M3's speech
        # path constructs its Agent with allow_retry=False (spec split-path).
        deep_llm=deep_llm,
        deep_tools=deep_tools,
        context_budget_tokens=settings.context.budget_tokens,
        context_keep_last=settings.context.keep_last_messages,
    )
    # The repo map ranks up whatever the live turn is about, which only the
    # Agent knows — and the Agent cannot exist until the belt does. Patched
    # explicitly rather than closed over a not-yet-bound name, so the ordering
    # dependency is visible instead of being a NameError waiting to happen.
    for belt in (tools, deep_tools):
        for tool in belt:
            if isinstance(tool, RepoMapTool):
                tool._mentions = lambda: agent._mentions_now
    return agent


def collect_pending_grants(repo_root: Path) -> list[Grant]:
    """Everything this repo supplies that is not yet approved at its current
    contents: privileged config keys plus repo soul markdown."""
    return [*load_settings(repo_root).pending_grants, *pending_soul_grants(repo_root)]


def render_consent_prompt(grants: list[Grant], plugin_names: list[str]) -> str:
    lines = ["This repo wants permissions Pyrrhon does not grant by default:"]
    lines += [f"  {grant.effect}" for grant in grants]
    if plugin_names:
        lines.append(f"  run plugin code: {', '.join(plugin_names)}")
    lines.append("Allow for this repo? [y/N]")
    return "\n".join(lines)


def _repo_code_allowed(repo_root: Path, manager: PluginManager) -> bool:
    """Whether load_all() may execute repo-level plugin code.

    All-or-nothing, matching the gate this replaces: load_all takes a single
    flag for every repo plugin, so "any one is trusted" would run the code of
    plugins the user never saw. A repo that adds a second plugin after the
    first was approved must be asked again.
    """
    names = manager.repo_code_plugins()
    if not names:
        return False  # nothing executable at repo level; the flag is irrelevant
    trusted = read_trust_file(repo_root).plugins
    return all(name in trusted for name in names)


def load_channel_plugins(
    repo_root: Path,
    ask: Callable[[str], bool],
    trust_repo: bool = False,
    interactive: bool = True,
    home: Path | None = None,
) -> tuple[list[LoadedPlugin], Settings]:
    """Channel startup: one consent gate -> plugins -> granted settings.

    Everything a cloned repo can hand us that runs code, redirects egress, or
    writes the system prompt goes through this single prompt. Refusal is never
    fatal: Pyrrhon opens with the grants it has. A non-interactive run refuses
    without prompting, because a blocked stdin would otherwise hang CI and an
    auto-yes would defeat the gate entirely.

    Runs synchronously before the event loop serves turns; both channels
    (REPL and TUI) call this so the trust flow and merge order stay identical.
    """
    manager = PluginManager(repo_root, home=home) if home else PluginManager(repo_root)
    settings = load_settings(repo_root, home)
    pending = [*settings.pending_grants, *pending_soul_grants(repo_root)]
    trusted_plugins = read_trust_file(repo_root).plugins
    plugin_names = [n for n in manager.repo_code_plugins() if n not in trusted_plugins]

    approved = False
    if pending or plugin_names:
        total = len(pending) + len(plugin_names)
        if trust_repo:
            log.warning(
                "--trust-repo: granting %d repo permission(s) without prompting", total
            )
            approved = True
        elif not interactive:
            log.warning(
                "no interactive terminal: refusing %d repo permission(s); "
                "pass --trust-repo to grant them",
                total,
            )
        else:
            approved = ask(render_consent_prompt(pending, plugin_names))
    if approved:
        record_grants(repo_root, pending)
        if plugin_names:
            record_trusted(repo_root, plugin_names)
        # Re-read: the grants just recorded are what make the quarantined
        # values loadable, and settings above was built before they existed.
        settings = load_settings(repo_root, home)

    plugins = manager.load_all(allow_repo_code=_repo_code_allowed(repo_root, manager))
    return plugins, merge_plugin_settings(settings, plugins)


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
            console.print(citation_markup(session.agent.repo_root, event))
        elif isinstance(event, AskUser):
            # Design mode's Socratic question, rendered distinctly (spec: M6).
            console.print(f"[bold magenta]? {event.question}[/bold magenta]")
