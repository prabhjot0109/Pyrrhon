"""Text REPL — the first (and thinnest) channel over the headless core."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from pyrrhon.commands.init_cmd import init_pyrrhon_dir
from pyrrhon.config.settings import load_settings
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.soul import build_system_prompt
from pyrrhon.core.events import Citation, SpeechChunk, ToolCallStarted
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import MissingAPIKeyError, create_llm
from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool


def build_agent(repo_root: Path, llm=None) -> Agent:
    settings = load_settings(repo_root)
    llm = llm or create_llm(settings.fast, settings)
    tools = [ReadFileTool(repo_root), GrepTool(repo_root), GlobTool(repo_root)]
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=build_system_prompt(repo_root),
        repo_root=repo_root,
        grounding_gate=GroundingGate(repo_root),
        # REPL is a screen channel → default allow_retry=True. M3's speech
        # path constructs its Agent with allow_retry=False (spec split-path).
    )


def run_repl(repo: str) -> None:
    console = Console()
    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        console.print(f"[red]Not a directory: {repo_root}[/red]")
        raise SystemExit(1)
    try:
        agent = build_agent(repo_root)
    except MissingAPIKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    console.print(
        f"[bold]Pyrrhon[/bold] — discussing [cyan]{repo_root.name}[/cyan]. "
        "Commands: /init (personalize), /quit"
    )
    try:
        asyncio.run(_repl_loop(agent, console, repo_root))
    except KeyboardInterrupt:
        pass


async def _repl_loop(agent: Agent, console: Console, repo_root: Path) -> None:
    history: list[dict] = []
    while True:
        try:
            user = (await asyncio.to_thread(console.input, "[bold cyan]you> [/bold cyan]")).strip()
        except EOFError:
            break
        if not user:
            continue
        if user in {"/quit", "/exit"}:
            break
        if user == "/init":
            path, created = init_pyrrhon_dir(repo_root)
            verb = "created" if created else "already exists"
            console.print(f"soul file {verb}: {path} — edit it, then restart the session.")
            continue
        async for event in agent.run_turn(history, user):
            if isinstance(event, ToolCallStarted):
                console.print(f"[dim]→ {event.name}({event.args})[/dim]")
            elif isinstance(event, SpeechChunk):
                console.print(Markdown(event.text))
            elif isinstance(event, Citation):
                console.print(f"[green]📍 {event.file}:{event.line}[/green]")
