"""The adapter driver: native provider APIs behind the same duck-typed interface."""

import pathlib
import re

import pytest

import pyrrhon.core
from pyrrhon.core.providers.adapters import AdapterLLM

_PIPECAT_IMPORT = re.compile(r"^\s*(from|import)\s+pipecat")


def test_core_imports_only_pipecat_adapters_never_the_frame_bus():
    """The layering exception is adapters-only and must not widen.

    Rooted at the package's own __file__ rather than a relative path, so it
    checks the tree it means to whatever the working directory is.
    """
    root = pathlib.Path(pyrrhon.core.__file__).parent
    offenders = [
        f"{path.relative_to(root)}: {line.strip()}"
        for path in root.rglob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if _PIPECAT_IMPORT.search(line) and "pipecat.adapters" not in line
    ]
    assert not offenders, "core/ may import pipecat.adapters ONLY:\n" + "\n".join(
        offenders
    )


def test_adapter_llm_exposes_the_agent_loop_interface():
    llm = AdapterLLM(model="m", api_key="k", adapter_module=None)
    assert llm.model == "m"
    assert hasattr(llm, "chat") and hasattr(llm, "stream")


def test_fallback_accepts_an_adapter_driver():
    """FallbackLLM must not care which driver it holds."""
    from pyrrhon.core.providers.llm import FallbackLLM

    chain = [AdapterLLM(model="a", api_key="k", adapter_module=None)]
    assert FallbackLLM(chain).model == "a"


async def test_the_seam_is_not_wired_up_yet_and_says_so():
    """chat/stream are deliberately unimplemented — the native message-shape
    translation is the check-in point in the M15b plan. Failing loudly beats
    failing subtly if someone wires this in before that work lands."""
    llm = AdapterLLM(model="m", api_key="k", adapter_module=None)
    with pytest.raises(NotImplementedError):
        await llm.chat([{"role": "user", "content": "hi"}])
    with pytest.raises(NotImplementedError):
        async for _ in llm.stream([{"role": "user", "content": "hi"}]):
            pass


def test_create_llm_never_hands_back_an_unimplemented_driver(tmp_path, monkeypatch):
    """Every row with a native_adapter must still resolve to a working client.

    This is what keeps the seam inert: a provider is selectable only because
    its OpenAI-compatible endpoint works today, never because an adapter
    module was named on its row.
    """
    from pyrrhon.config.settings import ModelSlot, Settings
    from pyrrhon.core.providers.llm import OpenAICompatLLM, create_llm
    from pyrrhon.core.providers.registry import LLM_PROVIDERS

    settings = Settings()
    for provider in LLM_PROVIDERS:
        if provider.native_adapter is None:
            continue
        monkeypatch.setenv(provider.api_key_env, "k")
        llm = create_llm(ModelSlot(provider=provider.id, model="m"), settings)
        assert isinstance(llm, OpenAICompatLLM)


def test_every_named_adapter_module_exists_on_disk():
    """Tier 1's idea applied to the LLM lane.

    `native_adapter` is a module path written as a string, so nothing checks it
    until someone implements chat() — by which point a pipecat rename looks
    like a bug in the new code. find_spec, not import: anthropic_adapter pulls
    the `anthropic` SDK, and a row whose SDK is absent is exactly the case this
    has to survive.
    """
    import importlib.util

    from pyrrhon.core.providers.registry import LLM_PROVIDERS

    missing = []
    for provider in LLM_PROVIDERS:
        if provider.native_adapter is None:
            continue
        try:
            found = importlib.util.find_spec(provider.native_adapter) is not None
        except ModuleNotFoundError:
            found = False
        if not found:
            missing.append(f"{provider.id}: {provider.native_adapter}")
    assert not missing, "rows naming an adapter pipecat no longer ships:\n" + "\n".join(
        missing
    )


def test_a_missing_provider_sdk_degrades_with_an_actionable_message():
    """The adapters carry no frame dependency, but they DO import the
    provider's own SDK — anthropic_adapter needs `anthropic`. Same error
    policy as voice/factory._load: say what is missing, never let a bare
    ModuleNotFoundError out."""
    from pyrrhon.core.providers.adapters import AdapterUnavailableError

    llm = AdapterLLM(
        model="m", api_key="k", adapter_module="pipecat.adapters.services.not_a_module"
    )
    with pytest.raises(AdapterUnavailableError) as exc:
        llm.load_adapter()
    assert "not_a_module" in str(exc.value)
    assert "OpenAI-compatible" in str(exc.value)


def test_loading_an_adapter_reaches_pipecat_and_caches_it():
    """The layering exception, exercised rather than asserted about.

    Gemini's adapter is the one whose SDK ships with Pyrrhon's base deps, so it
    is the row that can prove core/ really can reach pipecat.adapters.
    """
    from pyrrhon.core.providers.registry import find_llm

    gemini = find_llm("gemini")
    llm = AdapterLLM(model="m", api_key="k", adapter_module=gemini.native_adapter)
    module = llm.load_adapter()
    assert module is not None
    assert module.__name__ == gemini.native_adapter
    assert llm.load_adapter() is module  # imported once, then cached
