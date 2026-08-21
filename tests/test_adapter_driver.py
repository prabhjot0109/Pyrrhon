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
