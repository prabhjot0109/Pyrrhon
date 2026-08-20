"""Every provider a user can pick, as data — the wizard and /settings render this.

Sync rule (pinned by tests/test_catalog.py): LLM ids mirror BUILTIN_PROVIDERS.
The voice menus are no longer hand-maintained at all — stt_choices() and
tts_choices() are DERIVED from pyrrhon/voice/registry.py, so a provider cannot
be offered here and missing there. For TTS choices, default_model carries the
default VOICE, since a voice is the thing users actually pick.

availability() is the honesty rule: Pyrrhon may offer a provider it cannot
currently run, but it may never imply that it can.
"""

from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass
from importlib import metadata


@dataclass(frozen=True)
class ProviderChoice:
    id: str
    label: str
    key_env: str | None          # None: keyless (local server / on-device model)
    default_model: str | None    # LLM/STT: model id; TTS: default voice
    note: str = ""


LLM_CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("groq", "Groq", "GROQ_API_KEY", "llama-3.3-70b-versatile",
                   "fast open-weights inference; generous free tier"),
    ProviderChoice("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4o-mini",
                   "GPT models"),
    ProviderChoice("gemini", "Google Gemini", "GEMINI_API_KEY", "gemini-2.5-flash",
                   "gemini-2.5-pro for the deep slot"),
    ProviderChoice("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek-chat",
                   "deepseek-reasoner for deep reasoning"),
    ProviderChoice("cerebras", "Cerebras", "CEREBRAS_API_KEY", "llama-3.3-70b",
                   "fastest tokens/sec around"),
    ProviderChoice("openrouter", "OpenRouter", "OPENROUTER_API_KEY",
                   "deepseek/deepseek-chat", "one key, many models"),
    ProviderChoice("huggingface", "Hugging Face", "HF_TOKEN",
                   "meta-llama/Llama-3.3-70B-Instruct",
                   "HF Inference Providers router"),
    ProviderChoice("ollama", "Ollama (local)", None, "llama3.2",
                   "runs on your machine — `ollama pull <model>` first"),
    ProviderChoice("lmstudio", "LM Studio (local)", None, "local-model",
                   "uses whatever model LM Studio has loaded"),
)

def _providers(kind: str):
    """The voice table, imported INSIDE the function by design.

    pyrrhon/config/ must not depend on pyrrhon/voice/ at import time — that is
    the layering rule in CLAUDE.md, and its purpose is that config/ stays
    importable without the audio stack. A function-local import satisfies it:
    registry.py is pure stdlib data that names pipecat classes as strings and
    imports no pipecat itself, and nothing here runs until a menu is rendered.
    """
    from pyrrhon.voice.registry import stt_providers, tts_providers

    return stt_providers() if kind == "stt" else tts_providers()


def _extra_satisfied(extra: str) -> bool:
    """True if every distribution `pipecat-ai[extra]` pulls in is installed.

    Locating the module is NOT enough, and getting this wrong is the whole
    failure this function exists to prevent: pipecat ships every service
    module in the base wheel, so find_spec succeeds for providers whose
    third-party dependencies are absent — the module imports onnxruntime (or
    whatever) at import time and blows up. Asking pipecat's own metadata which
    distributions the extra requires answers "can this actually run" exactly,
    with no imports and no second list for someone to forget to update.
    """
    try:
        requirements = metadata.requires("pipecat-ai") or []
    except metadata.PackageNotFoundError:
        return False
    marker = f'extra == "{extra}"'
    names = [
        _requirement_name(req) for req in requirements
        if marker in req and not req.startswith("pipecat-ai[")
    ]
    for name in names:
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            return False
    return True


def _requirement_name(requirement: str) -> str:
    """'kokoro-onnx<1,>=0.5.0; extra == "kokoro"' -> 'kokoro-onnx'."""
    head = requirement.split(";", 1)[0].strip()
    return re.split(r"[<>=!~\[\s(]", head, maxsplit=1)[0]


def _installed(module: str) -> bool:
    """True if the module can be located on disk. Does not import it."""
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def availability(provider) -> str:
    """One of: 'ready', 'needs <ENV>', or 'install: <command>'."""
    runnable = _installed(provider.module) and (
        provider.extra is None or _extra_satisfied(provider.extra)
    )
    if not runnable:
        if provider.extra:
            return f'install: uv add "pipecat-ai[{provider.extra}]"'
        return "install: unavailable"
    if provider.key_env and not os.environ.get(provider.key_env):
        return f"needs {provider.key_env}"
    return "ready"


def _to_choice(provider) -> ProviderChoice:
    return ProviderChoice(
        id=provider.id,
        label=provider.label,
        key_env=provider.key_env,
        default_model=provider.default_voice,
        note=provider.note,
    )


def stt_choices() -> tuple[ProviderChoice, ...]:
    return tuple(_to_choice(p) for p in _providers("stt"))


def tts_choices() -> tuple[ProviderChoice, ...]:
    return tuple(_to_choice(p) for p in _providers("tts"))
