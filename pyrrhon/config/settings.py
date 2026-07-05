"""Load and merge Pyrrhon settings: global ~/.pyrrhon/config.toml then <repo>/.pyrrhon.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class ModelSlot(BaseModel):
    provider: str
    model: str


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str


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
}


class VoiceSettings(BaseModel):
    """M3 voice-channel knobs (TOML section [voice])."""

    stt_model: str = "whisper-large-v3-turbo"  # Groq Whisper model
    tts_voice: str = "nova"                    # OpenAI TTS voice
    chars_per_sec: float = 15.0                # played-text estimator rate


class Settings(BaseModel):
    fast: ModelSlot = ModelSlot(provider="groq", model="llama-3.3-70b-versatile")
    deep: ModelSlot | None = None
    providers: dict[str, ProviderConfig] = {}
    voice: VoiceSettings = VoiceSettings()

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
