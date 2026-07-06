"""Load and merge Pyrrhon settings: global ~/.pyrrhon/config.toml then <repo>/.pyrrhon.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, model_validator


class ModelSlot(BaseModel):
    provider: str
    model: str


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str = ""  # empty: provider needs no key (local servers)


class MCPServerConfig(BaseModel):
    """One [mcp_servers.<name>] table: a stdio command OR a streamable-HTTP url."""

    command: str | None = None
    args: list[str] = []
    url: str | None = None

    @model_validator(mode="after")
    def _exactly_one_transport(self) -> "MCPServerConfig":
        if (self.command is None) == (self.url is None):
            raise ValueError(
                "an MCP server needs exactly one of 'command' (stdio) or "
                "'url' (streamable HTTP)"
            )
        return self


BUILTIN_PROVIDERS: dict[str, ProviderConfig] = {
    "openai": ProviderConfig(base_url=None, api_key_env="OPENAI_API_KEY"),
    "groq": ProviderConfig(
        base_url="https://api.groq.com/openai/v1", api_key_env="GROQ_API_KEY"
    ),
    "openrouter": ProviderConfig(
        base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY"
    ),
    "cerebras": ProviderConfig(
        base_url="https://api.cerebras.ai/v1", api_key_env="CEREBRAS_API_KEY"
    ),
    "gemini": ProviderConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
    ),
    "deepseek": ProviderConfig(
        base_url="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY"
    ),
    "huggingface": ProviderConfig(
        base_url="https://router.huggingface.co/v1", api_key_env="HF_TOKEN"
    ),
    "ollama": ProviderConfig(base_url="http://localhost:11434/v1", api_key_env=""),
    "lmstudio": ProviderConfig(base_url="http://localhost:1234/v1", api_key_env=""),
}


class VoiceSettings(BaseModel):
    """M3/M8/M9 voice-channel knobs (TOML section [voice]).

    stt_provider: groq | openai | gemini | deepgram | whisper-local
    tts_provider: openai | gemini | cartesia | elevenlabs | deepgram | piper
    stt_model / tts_voice are provider-specific; when unset each provider
    applies its own sensible default (see pyrrhon/voice/providers.py).
    Cartesia and ElevenLabs have no meaningful default voice — they require
    an explicit tts_voice (a voice id from your account).
    """

    stt_provider: str = "groq"
    stt_model: str | None = None               # provider default when unset
    tts_provider: str = "openai"
    tts_model: str | None = None               # provider default when unset
    tts_voice: str | None = None               # provider default when unset
    tts_url: str | None = None                 # local TTS server (piper HTTP mode)
    chars_per_sec: float = 15.0                # played-text estimator rate


class ContextSettings(BaseModel):
    """Context-window budgeting (TOML section [context])."""

    budget_tokens: int = 32000       # estimated-token ceiling before compaction
    keep_last_messages: int = 8      # recent messages kept verbatim


class Settings(BaseModel):
    fast: ModelSlot = ModelSlot(provider="groq", model="llama-3.3-70b-versatile")
    deep: ModelSlot | None = None
    providers: dict[str, ProviderConfig] = {}
    voice: VoiceSettings = VoiceSettings()
    context: ContextSettings = ContextSettings()
    mcp_servers: dict[str, MCPServerConfig] = {}
    # Slot name ("fast"/"deep") -> providers tried IN ORDER after the slot's
    # primary. Entry format: "provider" or "provider/model" (first '/' splits).
    fallbacks: dict[str, list[str]] = {}

    @property
    def deep_slot(self) -> ModelSlot:
        # Spec rule: the deep slot falls back to the fast slot when unset.
        return self.deep or self.fast

    def provider_for(self, slot: ModelSlot) -> ProviderConfig:
        if slot.provider in self.providers:
            return self.providers[slot.provider]
        if slot.provider in BUILTIN_PROVIDERS:
            return BUILTIN_PROVIDERS[slot.provider]
        raise KeyError(
            f"Unknown provider '{slot.provider}'. Add [providers.{slot.provider}] "
            f"to .pyrrhon.toml or use one of: {', '.join(BUILTIN_PROVIDERS)}"
        )


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings(repo_root: Path, home: Path | None = None) -> Settings:
    home = home or Path.home()
    merged = {
        **_read_toml(home / ".pyrrhon" / "config.toml"),
        **_read_toml(repo_root / ".pyrrhon.toml"),
    }
    return Settings.model_validate(merged)
