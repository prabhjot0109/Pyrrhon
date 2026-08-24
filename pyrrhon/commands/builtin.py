"""Built-in slash commands: /init, /model, /code, /exit.

Importing this module registers them (the @command decorator writes into
the registry table); channels do:

    from pyrrhon.commands import builtin  # noqa: F401
"""

from __future__ import annotations

from shutil import which
from subprocess import Popen

from pyrrhon.commands.init_cmd import init_pyrrhon_dir
from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.config.settings import ModelSlot, load_settings
from pyrrhon.core.providers.llm import MissingAPIKeyError, create_llm


@command("init", "Scaffold .pyrrhon/soul.md so Pyrrhon knows who you are")
def init_command(args: str, ctx: CommandContext) -> str:
    path, created = init_pyrrhon_dir(ctx.repo_root)
    verb = "created" if created else "already exists"
    return f"soul file {verb}: {path} — edit it, then restart the session."


@command("model", "Switch a model slot: /model <fast|deep> <provider>/<model>")
def model_command(args: str, ctx: CommandContext) -> str:
    usage = "ERROR: usage: /model <fast|deep> <provider>/<model>"
    parts = args.split()
    if len(parts) != 2 or "/" not in parts[1]:
        return usage
    slot_name, spec = parts
    if slot_name not in ("fast", "deep"):
        return usage
    # First path segment is the provider; the rest is the model (OpenRouter
    # model ids contain slashes, e.g. deepseek/deepseek-r1).
    provider, _, model = spec.partition("/")
    if not model:
        return usage
    settings = load_settings(ctx.repo_root)
    try:
        llm = create_llm(ModelSlot(provider=provider, model=model), settings)
    except (KeyError, MissingAPIKeyError) as exc:
        return f"ERROR: {exc}"
    if slot_name == "fast":
        ctx.agent.llm = llm
        return f"fast slot is now {provider}/{model}."
    # Through the seam, not a bare assignment: think_deeper captured the model
    # at construction, so writing the attribute alone left escalation calling
    # the old one while this line claimed otherwise.
    ctx.agent.set_deep_llm(llm)
    return f"deep slot is now {provider}/{model}."


@command("code", "Open the current citation in VS Code")
def code_command(args: str, ctx: CommandContext) -> str:
    citation = getattr(ctx.ui, "last_citation", None)
    if citation is None:
        return "ERROR: no citation to open yet — ask about the code first."
    exe = which("code")
    if exe is None:
        return "ERROR: VS Code CLI ('code') not found on PATH."
    target = f"{ctx.repo_root / citation.file}:{citation.line or 1}"
    try:
        # Popen (not run): fire-and-forget, never blocks the channel's loop.
        Popen([exe, "--goto", target])
    except OSError as exc:
        return f"ERROR: could not launch VS Code: {exc}"
    return f"Opened {citation.file}:{citation.line or 1} in VS Code."


@command("quit", "Leave Pyrrhon")
@command("exit", "Leave Pyrrhon")
def exit_command(args: str, ctx: CommandContext) -> str:
    """Registered, rather than intercepted by each channel's read loop.

    The REPL matched the two names against its own input string before
    dispatch ever ran, so the one table that drives `/help`, the inline `/`
    menu and the command palette had never heard of them — and the TUI, which
    has no read loop to intercept anything, simply had no way out but a
    control key. One row here gives both channels the command, and gives it a
    line in every list that claims to enumerate the commands.

    A handler returns a string and never raises, so it asks the channel to
    stop rather than stopping it: `ctx.ui` is duck-typed, exactly as `/code`
    already treats `last_citation`.
    """
    request_exit = getattr(ctx.ui, "request_exit", None)
    if request_exit is None:
        return "ERROR: this channel has no way to exit — press ctrl+c."
    request_exit()
    return "Leaving. Bye."
