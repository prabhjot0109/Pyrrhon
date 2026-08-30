"""Composition root — assembles an Agent and everything a channel needs first.

This module sits ABOVE the channels, not inside one. Every channel (REPL,
TUI, voice) and both eval harnesses call `build_agent`; none of them should
have to import another channel to start up, which is what happened while this
code lived in `pyrrhon/repl.py`.

Two responsibilities, in the order a channel needs them:

1. `load_channel_plugins` — the startup trust gate. Everything a cloned repo
   supplies that runs code, redirects egress, or writes the system prompt is
   approved here, once, before the event loop serves a turn.
2. `build_agent` — the tool belt, the LLM slots, the grounding gate, and the
   system prompt, wired into one Agent.

Plus the background warm-ups, which are startup concerns rather than channel
ones: they exist to keep the first turn off the cold path.

Nothing here imports a channel. That direction is the layering rule the rest
of the package already follows (`pyrrhon/core/` imports nothing outward), and
keeping it means a new channel needs no edits to an existing one.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from pyrrhon.config.settings import Settings, load_settings
from pyrrhon.config.trust import Grant, read_trust_file, record_grants
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.soul import build_system_prompt, pending_soul_grants
from pyrrhon.core.events import ScreenArtifact
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import (
    MissingAPIKeyError,
    create_llm,
    create_llm_with_fallbacks,
)
from pyrrhon.core.tools.ast_index import (
    DependenciesTool,
    FindSymbolTool,
    RepoMapTool,
    SymbolIndex,
)
from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool
from pyrrhon.core.tools.images import ReadImageTool
from pyrrhon.core.tools.memory import RememberTool
from pyrrhon.core.tools.orientation import build_orientation
from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool
from pyrrhon.core.tools.results import ReadResultTool, ResultStore
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

log = logging.getLogger("pyrrhon.bootstrap")

# What the deep subagent does NOT inherit from the builtin belt. Named as an
# exclusion rather than a second hand-written list because the two belts used
# to be maintained side by side: adding a read-only tool to one and forgetting
# the other silently gave think_deeper a weaker belt than the main loop, with
# no test to catch it. Derivation makes that drift impossible.
#
# think_deeper itself is absent because Agent.__init__ registers it, so it is
# never in the builtin list this filters.
DEEP_EXCLUDED = frozenset({
    "write_spec",    # the deep pass is read-only
    "remember",      # the fast model owns memory
    "web_search",    # repo questions stay in the repo
    "web_fetch",
    # The deep subagent's ToolGuard has no store, so its own oversized results
    # still truncate — a pager on its belt could only follow pointers the FAST
    # loop minted, which is not what a bounded read-only pass is for. Its
    # budget bounds it anyway; paging would only let it spend that budget on
    # tails it did not fetch.
    "read_result",
})


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


def _build_vision_llm(settings: Settings):
    """The LLM read_image asks, or None when nothing is configured to see.

    No fallback chain: a chain exists so a spoken turn survives a provider
    outage, and a tool call that returns "ERROR: ..." already degrades
    gracefully. A missing key is the same — the tool says so in words.
    """
    slot = settings.vision_slot()
    if slot is None:
        return None
    try:
        return create_llm(slot, settings)
    except (KeyError, MissingAPIKeyError) as exc:
        log.info("vision slot unavailable, read_image will say so: %s", exc)
        return None


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
    # The vision slot is optional, and read_image is registered either way:
    # with none configured it returns an actionable ERROR telling the user how
    # to enable it. Registering it conditionally would make the belt vary by
    # config, which tests/test_safety.py deliberately forbids.
    vision_llm = _build_vision_llm(settings)
    index = SymbolIndex(repo_root)
    # The reviewed, in-tree belt. Kept as its own list because the deep
    # subagent's belt is derived from it below — MCP adapters and plugin tools
    # join `tools` afterwards and are deliberately NOT inherited (uncontrolled
    # cost, and neither is covered by the safety review).
    builtin_tools = [
        ReadFileTool(repo_root),
        ReadImageTool(repo_root, vision_llm),
        GrepTool(repo_root),
        GlobTool(repo_root),
        # Holds the session's persisted-result store. The Agent reads the
        # store back OFF this tool rather than from a field of its own, so a
        # result is written exactly when the pager is on the belt to read it.
        ReadResultTool(ResultStore(repo_root)),
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
    ]
    tools = [*builtin_tools, *(extra_tools or [])]  # MCP adapters join here
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
    # Derived, not hand-listed — see DEEP_EXCLUDED. Instances are shared with
    # the main belt rather than rebuilt: every tool is immutable after
    # construction (the only mutable state, the parse cache and its locks,
    # lives on SymbolIndex, which both belts already shared).
    deep_tools = [t for t in builtin_tools if t.name not in DEEP_EXCLUDED]
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
    # One pass covers both belts now that deep_tools shares their instances.
    for tool in tools:
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


def start_channel(
    repo: str,
    serve: Callable[[Agent, MCPManager, list[LoadedPlugin]], Awaitable[None]],
    ask: Callable[[str], bool],
    report: Callable[[str], None],
    trust_repo: bool = False,
) -> tuple[list[LoadedPlugin], Settings]:
    """Run a channel's whole startup, then hand it a live Agent.

    Both screen channels had this sequence written out separately, in the same
    order, for the same reasons — and the order is load-bearing at three
    points, so a divergence between the copies would have been a subtle bug
    rather than an obvious one:

      * the consent gate runs BEFORE the event loop, because the settings it
        unlocks decide which MCP servers get started;
      * `interactive` comes from isatty(), because piped stdin must refuse
        rather than block;
      * MCPManager.start() and .stop() must be awaited from the SAME task
        (anyio cancel-scope rule), which is why the agent is built inside
        `asyncio.run` and not handed in.

    `serve` receives the assembled Agent, the running manager, and the loaded
    plugins, and owns the channel's main loop. `report` prints a fatal message
    in whatever styling the channel uses. Returns the plugins and merged
    settings for any caller that needs them after the loop exits.
    """
    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        report(f"Not a directory: {repo_root}")
        raise SystemExit(1)

    from pyrrhon.config.wizard import ensure_configured

    ensure_configured()  # stored keys → env; first run offers the wizard
    plugins, settings = load_channel_plugins(
        repo_root, ask, trust_repo=trust_repo, interactive=sys.stdin.isatty()
    )

    async def _main() -> None:
        warmup: asyncio.Task | None = None
        manager = MCPManager(settings.mcp_servers)
        mcp_tools = await manager.start()  # never raises; dead servers log once
        try:
            agent = build_agent(
                repo_root, extra_tools=mcp_tools, settings=settings, plugins=plugins
            )
            # After build_agent, deliberately: the pool worth warming is the
            # one behind the CONFIGURED base URL, and warming a default before
            # settings resolve is a silent no-op that looks like a win.
            # Duck-typed, so a channel handed a test double simply skips it.
            warm = getattr(agent.llm, "preconnect", None)
            warmup = warm() if warm is not None else None
            await serve(agent, manager, plugins)
        finally:
            if warmup is not None and not warmup.done():
                warmup.cancel()
                try:
                    await warmup
                except (asyncio.CancelledError, Exception):
                    pass  # teardown never fails on a warmup nobody waited for
            await manager.stop()  # same task as start() — anyio cancel-scope rule

    try:
        asyncio.run(_main())
    except MissingAPIKeyError as exc:
        report(str(exc))
        # `from exc`: the message is already printed, but chaining keeps the
        # cause honest for anyone who runs this under a debugger.
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        pass
    return plugins, settings
