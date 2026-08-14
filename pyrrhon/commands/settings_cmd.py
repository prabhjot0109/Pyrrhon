"""/settings — show OR change what Pyrrhon is configured to use.

Read-only with no args (providers, models, masked key status). With a
subcommand it edits the global config.toml (and, for keys, the owner-only
credentials.toml) so the whole setup is reachable without restarting into the
wizard. Key *values* are never rendered — only a masked fingerprint.
"""

from __future__ import annotations

import os

from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.config.catalog import LLM_CHOICES, STT_CHOICES, TTS_CHOICES
from pyrrhon.config.credentials import read_credentials, save_credentials
from pyrrhon.config.settings import ModelSlot, load_settings, patch_config
from pyrrhon.core.providers.llm import MissingAPIKeyError, create_llm

_KEY_ENVS = sorted(
    {c.key_env for c in (*LLM_CHOICES, *STT_CHOICES, *TTS_CHOICES) if c.key_env}
)


def _mask(value: str) -> str:
    """A fingerprint that proves the key landed without revealing it."""
    return "****" if len(value) <= 8 else f"{value[:3]}…{value[-4:]}"


def _key_line(env: str, stored: dict[str, str]) -> str:
    if os.environ.get(env):
        return f"  {env}: env"
    if env in stored:
        return f"  {env}: stored (~/.pyrrhon/credentials.toml)"
    return f"  {env}: missing"


def _show(ctx: CommandContext) -> str:
    settings = load_settings(ctx.repo_root)
    stored = read_credentials()
    deep = settings.deep_slot
    lines = [
        f"fast:  {settings.fast.provider}/{settings.fast.model}",
        f"deep:  {deep.provider}/{deep.model}"
        + ("" if settings.deep else "  (= fast; set with /settings llm deep …)"),
        f"stt:   {settings.voice.stt_provider}"
        + (f" ({settings.voice.stt_model})" if settings.voice.stt_model else ""),
        f"tts:   {settings.voice.tts_provider}"
        + (f" ({settings.voice.tts_voice})" if settings.voice.tts_voice else ""),
        "keys:",
    ]
    lines += [_key_line(env, stored) for env in _KEY_ENVS]
    lines += [
        "change it live:",
        "  /settings llm <fast|deep> <provider>/<model>",
        "  /settings stt <provider> [model]",
        "  /settings tts <provider> [voice]",
        "  /settings key <ENV_VAR> <value>   (stored owner-only; value hidden)",
        "or run the full wizard: pyrrhon --setup",
    ]
    return "\n".join(lines)


def _set_llm(ctx: CommandContext, rest: list[str]) -> str:
    usage = "ERROR: usage: /settings llm <fast|deep> <provider>/<model>"
    if len(rest) != 2 or rest[0] not in ("fast", "deep") or "/" not in rest[1]:
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
        else:
            agent.set_deep_llm(llm)
    return f"{slot_name} slot is now {provider}/{model} — saved and active."


def _set_voice(ctx: CommandContext, kind: str, rest: list[str]) -> str:
    choices = STT_CHOICES if kind == "stt" else TTS_CHOICES
    valid = {c.id for c in choices}
    if not rest or rest[0] not in valid:
        return (
            f"ERROR: usage: /settings {kind} <provider> "
            f"[{'model' if kind == 'stt' else 'voice'}]  "
            f"(providers: {', '.join(sorted(valid))})"
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
    save_credentials({env: value})
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
