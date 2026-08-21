"""/settings — show OR change what Pyrrhon is configured to use.

Read-only with no args (providers, models, masked key status). With a
subcommand it edits the global config.toml (and, for keys, the owner-only
credentials.toml) so the whole setup is reachable without restarting into the
wizard. Key *values* are never rendered — only a masked fingerprint.
"""

from __future__ import annotations

import os

from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.config.catalog import (
    availability,
    llm_choices,
    stt_choices,
    tts_choices,
)
from pyrrhon.config.credentials import read_credentials, save_credentials
from pyrrhon.config.settings import ModelSlot, load_settings, patch_config
from pyrrhon.core.providers.llm import MissingAPIKeyError, create_llm
from pyrrhon.voice.registry import find

_KEY_ENVS = sorted(
    {c.key_env for c in (*llm_choices(), *stt_choices(), *tts_choices()) if c.key_env}
)


def _state_suffix(kind: str, provider_id: str) -> str:
    """What the user must do before this provider works, or nothing.

    Silent when the answer is 'ready': a [ready] badge on every line is noise,
    and the only reason to render state at all is to name an action.
    """
    provider = find(kind, provider_id)
    if provider is None:
        return "  [unknown provider]"
    state = availability(provider)
    return "" if state == "ready" else f"  [{state}]"


def _mask(value: str) -> str:
    """A fingerprint that proves the key landed without revealing it."""
    return "****" if len(value) <= 8 else f"{value[:3]}…{value[-4:]}"


def _key_line(env: str, stored: dict[str, str]) -> str:
    if os.environ.get(env):
        return f"  {env}: env"
    if env in stored:
        return f"  {env}: stored (~/.pyrrhon/credentials.toml)"
    return f"  {env}: missing"


def _vision_line(settings) -> str:
    """What read_image would use — including the case where nothing can see,
    which is the one worth saying out loud."""
    slot = settings.vision_slot()
    if slot is None:
        return "vision: none — read_image is off (/settings llm vision …)"
    suffix = "" if settings.vision else "  (= fast)"
    return f"vision: {slot.provider}/{slot.model}{suffix}"


def _show(ctx: CommandContext) -> str:
    settings = load_settings(ctx.repo_root)
    stored = read_credentials()
    deep = settings.deep_slot
    lines = [
        f"fast:   {settings.fast.provider}/{settings.fast.model}",
        f"deep:   {deep.provider}/{deep.model}"
        + ("" if settings.deep else "  (= fast; set with /settings llm deep …)"),
        _vision_line(settings),
        f"stt:    {settings.voice.stt_provider}"
        + (f" ({settings.voice.stt_model})" if settings.voice.stt_model else "")
        + _state_suffix("stt", settings.voice.stt_provider),
        f"tts:    {settings.voice.tts_provider}"
        + (f" ({settings.voice.tts_voice})" if settings.voice.tts_voice else "")
        + _state_suffix("tts", settings.voice.tts_provider),
        "keys:",
    ]
    lines += [_key_line(env, stored) for env in _KEY_ENVS]
    lines += [
        "change it live:",
        "  /settings llm <fast|deep|vision> <provider>/<model>",
        "  /settings stt <provider> [model]",
        "  /settings tts <provider> [voice]",
        "  /settings key <ENV_VAR> <value>   (stored owner-only; value hidden)",
        "or run the full wizard: pyrrhon --setup",
    ]
    return "\n".join(lines)


_LLM_SLOTS = ("fast", "deep", "vision")


def _set_llm(ctx: CommandContext, rest: list[str]) -> str:
    usage = "ERROR: usage: /settings llm <fast|deep|vision> <provider>/<model>"
    if len(rest) != 2 or rest[0] not in _LLM_SLOTS or "/" not in rest[1]:
        return usage
    slot_name = rest[0]
    provider, _, model = rest[1].partition("/")  # model ids can contain '/'
    if not provider or not model:
        return usage
    settings = load_settings(ctx.repo_root)
    slot = ModelSlot(provider=provider, model=model)
    try:
        llm = create_llm(slot, settings)
    except KeyError as exc:
        return f"ERROR: {exc}"  # unknown provider — nothing saved
    except MissingAPIKeyError as exc:
        patch_config({slot_name: {"provider": provider, "model": model}})
        return (
            f"Saved {slot_name} = {provider}/{model}, but {exc} "
            f"Add it with: /settings key <ENV_VAR> <value>."
        )
    patch_config({slot_name: {"provider": provider, "model": model}})
    agent = getattr(ctx, "agent", None)
    if agent is not None:
        if slot_name == "fast":
            agent.llm = llm
        elif slot_name == "deep":
            agent.set_deep_llm(llm)
        else:
            # The vision LLM lives on the tool, not on the Agent — read_image
            # makes its own call. Repointing it here is what makes the ERROR
            # that tool returns ("set one with /settings llm vision …") true
            # without a restart.
            _repoint_read_image(agent, llm)
    return f"{slot_name} slot is now {provider}/{model} — saved and active."


def _repoint_read_image(agent, llm) -> None:
    tool = getattr(agent, "tools", {}).get("read_image")
    if tool is not None:
        tool.llm = llm


def _set_voice(ctx: CommandContext, kind: str, rest: list[str]) -> str:
    choices = stt_choices() if kind == "stt" else tts_choices()
    valid = {c.id for c in choices}
    if not rest or rest[0] not in valid:
        # Every provider, each with what it would take to run — offering one
        # Pyrrhon cannot start without saying so is the failure this replaces.
        listed = "\n".join(
            f"  {c.id:<14} [{availability(find(kind, c.id))}]  {c.note}"
            for c in choices
        )
        return (
            f"ERROR: usage: /settings {kind} <provider> "
            f"[{'model' if kind == 'stt' else 'voice'}]\n{listed}"
        )
    provider = rest[0]
    choice = next(c for c in choices if c.id == provider)
    detail = rest[1] if len(rest) > 1 else choice.default_model
    detail_key = "stt_model" if kind == "stt" else "tts_voice"
    # None clears the key so the registry's per-provider default applies.
    patch_config({"voice": {f"{kind}_provider": provider, detail_key: detail}})

    # Point the running VoiceController at the new config (applies next /voice on).
    voice = getattr(ctx, "voice", None)
    if voice is not None and hasattr(voice, "update_settings"):
        voice.update_settings(load_settings(ctx.repo_root))

    msg = f"{kind} is now {provider}" + (f" ({detail})" if detail else "")
    stored = read_credentials()
    if choice.key_env and not (os.environ.get(choice.key_env) or choice.key_env in stored):
        msg += f" — needs {choice.key_env}; add it with /settings key {choice.key_env} <value>"
    else:
        msg += " — saved; toggle /voice off then on to apply."
    return msg


def _set_key(rest: list[str]) -> str:
    if len(rest) < 2:
        return "ERROR: usage: /settings key <ENV_VAR> <value>"
    env, value = rest[0].upper(), rest[1]
    try:
        save_credentials({env: value})
    except ValueError as exc:
        # A name the store cannot write. Surfacing it beats crashing the
        # channel — the user typed this, so they can retype it.
        return f"ERROR: {exc}"
    os.environ[env] = value  # usable this session immediately
    note = "" if env in _KEY_ENVS else f"  (heads up: {env} isn't a key Pyrrhon uses)"
    return (
        f"Stored {env} = {_mask(value)} in ~/.pyrrhon/credentials.toml "
        f"(owner-only). Active now.{note}"
    )


@command("settings", "Show or change providers, models, and API keys")
def settings_command(args: str, ctx: CommandContext) -> str:
    parts = args.split()
    if not parts:
        return _show(ctx)
    sub, rest = parts[0], parts[1:]
    if sub == "llm":
        return _set_llm(ctx, rest)
    if sub in ("stt", "tts"):
        return _set_voice(ctx, sub, rest)
    if sub == "key":
        return _set_key(rest)
    return (
        f"ERROR: unknown /settings subcommand '{sub}'. "
        "Use: llm | stt | tts | key, or /settings with no args to view."
    )
