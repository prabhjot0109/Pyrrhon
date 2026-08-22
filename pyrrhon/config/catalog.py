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

import ast
import importlib.util
import os
import pathlib
import sys
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ProviderChoice:
    id: str
    label: str
    key_env: str | None          # None: keyless (local server / on-device model)
    default_model: str | None    # STT: model id; TTS: default voice; LLM: None
    note: str = ""
    # What it would take to run this, from availability(). Empty for LLM rows:
    # an OpenAI-compatible endpoint needs no extra, so the key is the whole
    # story there and the wizard's own key check already tells it.
    state: str = ""


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


def _dependencies_present(module: str) -> bool:
    """True if everything `module` imports at the top level is installed.

    Locating the module is NOT enough, and getting this wrong is the whole
    failure this function exists to prevent: pipecat ships every service module
    in the base wheel, so find_spec succeeds for providers whose third-party
    dependencies are absent — the module imports onnxruntime (or whatever) at
    import time and blows up.

    Asking the MODULE what it imports, rather than asking pipecat's metadata
    what an extra pulls in, is both cheaper and exactly right, because an extra
    is coarser than a row. `pipecat-ai[deepgram]` covers two modules with
    different needs: the STT service imports the vendor SDK, the TTS service is
    plain HTTP over aiohttp and runs with nothing extra installed. The metadata
    question marked Deepgram TTS uninstallable while tier 3 was making it
    speak.

    Full dotted paths, never just the root package, and that is load-bearing:
    `google` is a namespace package, so find_spec("google") succeeds on a
    machine with nothing under it and Gemini TTS would be reported ready when
    it cannot import. Checking `google.api_core` is what keeps this honest.

    Parsed, not imported — importing is what we are trying to avoid finding out
    the hard way.
    """
    for name in _imports_of(module):
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def _toplevel_imports(source: str) -> set[str]:
    """Every non-stdlib, non-pipecat module `source` imports by absolute path."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return {
        name for name in names
        if name.split(".")[0] not in sys.stdlib_module_names
        and name.split(".")[0] != "pipecat"
    }


@lru_cache(maxsize=None)
def _imports_of(module: str) -> frozenset[str]:
    """What `module` imports, read off disk once per process.

    The read-and-parse is cached, the find_spec lookups in the caller are not:
    what a module imports cannot change while Pyrrhon runs, but whether those
    imports resolve can, if the user installs an extra mid-session.

    A module that is missing or unreadable yields the sentinel below, so the
    caller reports it unrunnable rather than vacuously fine.
    """
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError:
        return _UNREADABLE
    if spec is None or not spec.origin:
        return _UNREADABLE
    try:
        source = pathlib.Path(spec.origin).read_text(encoding="utf-8")
        return frozenset(_toplevel_imports(source))
    except (OSError, SyntaxError, ValueError):
        return _UNREADABLE  # a menu render must never raise over one bad row


# A module name that cannot resolve, so an unreadable module fails the check
# instead of passing it with an empty import set.
_UNREADABLE = frozenset({"pyrrhon.this.module.could.not.be.read"})


def _installed(module: str) -> bool:
    """True if the module can be located on disk. Does not import it."""
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def availability(provider) -> str:
    """One of: 'ready', 'ready, unverified', 'needs <ENV>', 'install: <cmd>'.

    `ready` is the strongest claim this file makes, so it means all three of
    installed, keyed, and *smoke-tested against the live service* — the
    provider.verified flag, which is set from a tier 3 run and nothing else.

    The fourth state exists because Pyrrhon curates more providers than anyone
    holds keys for. The alternative to shipping a row unverified is shipping
    fewer rows, and the alternative to labelling it is claiming a readiness
    nobody checked. Honest beats broad, and this is what makes it honest.
    """
    runnable = _installed(provider.module) and _dependencies_present(provider.module)
    if not runnable:
        if provider.extra:
            return f'install: uv add "pipecat-ai[{provider.extra}]"'
        return "install: unavailable"
    if provider.key_env and not os.environ.get(provider.key_env):
        return f"needs {provider.key_env}"
    return "ready" if getattr(provider, "verified", False) else "ready, unverified"


def _to_choice(provider) -> ProviderChoice:
    return ProviderChoice(
        id=provider.id,
        label=provider.label,
        key_env=provider.key_env,
        default_model=provider.default_voice,
        note=provider.note,
        state=availability(provider),
    )


def stt_choices() -> tuple[ProviderChoice, ...]:
    return tuple(_to_choice(p) for p in _providers("stt"))


def tts_choices() -> tuple[ProviderChoice, ...]:
    return tuple(_to_choice(p) for p in _providers("tts"))
