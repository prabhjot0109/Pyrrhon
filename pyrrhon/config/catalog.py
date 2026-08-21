"""Every provider a user can pick, as data — the wizard and /settings render this.

Nothing here is hand-maintained. llm_choices() is derived from
pyrrhon/core/providers/registry.py, stt_choices()/tts_choices() from
pyrrhon/voice/registry.py, and BUILTIN_PROVIDERS from the same LLM table — so a
provider cannot be offered in one view and missing from another.

default_model means different things per kind, which is why it is a field
rather than a rule: for TTS it carries the default VOICE (the thing users
actually pick), for STT a model id where the provider has a sensible one, and
for LLMs it is always None — see llm_choices().

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
    default_model: str | None    # STT: model id; TTS: default voice; LLM: None
    note: str = ""


def llm_choices() -> tuple[ProviderChoice, ...]:
    """The LLM menu, DERIVED from the provider table.

    default_model is deliberately None for every row: unlike a TTS voice,
    a chat model id has no defensible default here — the previous hand-written
    tuple still offered gpt-4o-mini and llama-3.3-70b-versatile in 2026. The
    wizard therefore requires the user to name a model, which is also what
    ModelSlot demands (`model: str`, no default).

    Imported inside the function for the same reason the voice table is: this
    keeps config/ importable without paying for anything it does not need.
    """
    from pyrrhon.core.providers.registry import LLM_PROVIDERS

    return tuple(
        ProviderChoice(
            id=p.id,
            label=p.label,
            key_env=p.api_key_env or None,
            default_model=None,
            note=p.note,
        )
        for p in LLM_PROVIDERS
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
