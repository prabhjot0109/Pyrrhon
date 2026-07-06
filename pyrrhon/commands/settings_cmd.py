"""/settings — what Pyrrhon is configured to use, and where each key comes from."""

from __future__ import annotations

import os

from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.config.catalog import LLM_CHOICES, STT_CHOICES, TTS_CHOICES
from pyrrhon.config.credentials import read_credentials
from pyrrhon.config.settings import load_settings


def _key_line(env: str, stored: dict[str, str]) -> str:
    if os.environ.get(env):
        return f"  {env}: env"
    if env in stored:
        return f"  {env}: stored (~/.pyrrhon/credentials.toml)"
    return f"  {env}: missing"


@command("settings", "Show providers, models, and API-key status")
def settings_command(args: str, ctx: CommandContext) -> str:
    settings = load_settings(ctx.repo_root)
    stored = read_credentials()
    deep = settings.deep_slot
    lines = [
        f"fast:  {settings.fast.provider}/{settings.fast.model}",
        f"deep:  {deep.provider}/{deep.model}"
        + ("" if settings.deep else "  (= fast; set [deep] to change)"),
        f"stt:   {settings.voice.stt_provider}"
        + (f" ({settings.voice.stt_model})" if settings.voice.stt_model else ""),
        f"tts:   {settings.voice.tts_provider}"
        + (f" ({settings.voice.tts_voice})" if settings.voice.tts_voice else ""),
        "keys:",
    ]
    envs = sorted({
        c.key_env
        for c in (*LLM_CHOICES, *STT_CHOICES, *TTS_CHOICES)
        if c.key_env is not None
    })
    lines += [_key_line(env, stored) for env in envs]
    lines.append("change any of this with: pyrrhon --setup")
    return "\n".join(lines)
