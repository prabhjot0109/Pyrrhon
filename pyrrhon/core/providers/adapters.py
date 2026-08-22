"""Native-API LLM drivers built on Pipecat's adapters.

STATUS: seam only. chat() and stream() raise NotImplementedError, and nothing
constructs this class — create_llm returns an OpenAICompatLLM for every row in
the provider table, including the two that name an adapter. What lands here is
the INTERFACE and the layering exception, both pinned by tests, so that the
native-provider work is an edit to two methods rather than a new architecture.
See the check-in point in the M15b plan.

Why adapters and not Pipecat's LLM *services*: a service only speaks through
the frame bus. Its one out-of-pipeline entry point, run_inference(), returns
`str | None` — no tool calls, no streaming — and adopting the frame path would
put the grounding gate downstream of speech. See Phase 6 of the M15 spec.

The adapters, by contrast, are pure data transformers:

    adapter.get_llm_invocation_params(context) -> provider-native params
    adapter.to_provider_tools_format(tools)    -> provider-native tools

That is the tedious per-provider translation worth not writing ourselves —
including Anthropic's prompt-cache markers, which the OpenAI-compat endpoint
does not offer at all — and it carries no frame dependency. This module is
therefore the ONE place pyrrhon/core may import pipecat, and only from
pipecat.adapters. tests/test_adapter_driver.py pins that.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from pyrrhon.core.providers.llm import LLMReply

_UNIMPLEMENTED = (
    "AdapterLLM is a seam: the native message-shape translation (Pyrrhon's "
    "list[dict] history to Pipecat's LLMContext) is not written yet. Use the "
    "provider's OpenAI-compatible endpoint, which create_llm already does."
)


class AdapterUnavailableError(RuntimeError):
    """The named adapter module cannot be imported on this machine."""


class AdapterLLM:
    """A native-API driver behind the agent loop's duck-typed LLM interface.

    Exposes exactly what loop.py and FallbackLLM use: `model`, `chat`, and
    `stream`. Anything beyond that is an implementation detail — keeping the
    surface this small is what lets a second driver exist without loop.py,
    FallbackLLM, or the grounding gate learning that it does.
    """

    def __init__(self, model: str, api_key: str, adapter_module: str | None) -> None:
        self.model = model
        self._api_key = api_key
        self._adapter_module = adapter_module
        self._adapter: ModuleType | None = None

    def load_adapter(self) -> ModuleType | None:
        """Import the adapter on first use, never at module import.

        By string, from LLMProvider.native_adapter, so pyrrhon/core carries no
        static pipecat dependency and stays importable without it installed.

        Pipecat's adapters carry no *frame* dependency, which is why they are
        the half of Phase 6 worth adopting — but they do import the provider's
        own SDK, so anthropic_adapter needs `anthropic` installed and gemini's
        needs `google-genai`. That is the same shape voice/factory._load
        handles, and it gets the same answer: name the install command instead
        of letting a bare ModuleNotFoundError reach the caller.
        """
        if self._adapter is None and self._adapter_module:
            try:
                self._adapter = importlib.import_module(self._adapter_module)
            except ImportError as exc:
                raise AdapterUnavailableError(
                    f"{self._adapter_module} needs a package that is not "
                    f"installed ({exc}). Install it, or stay on the provider's "
                    "OpenAI-compatible endpoint, which create_llm uses today."
                ) from exc
        return self._adapter

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def stream(self, messages: list[dict], tools: list[dict] | None = None):
        raise NotImplementedError(_UNIMPLEMENTED)
        yield  # pragma: no cover — makes this an async generator, not a coroutine
