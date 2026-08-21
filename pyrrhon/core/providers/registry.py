"""The LLM provider table: one row per provider, no code per provider.

Mirrors pyrrhon/voice/registry.py. `BUILTIN_PROVIDERS` and the wizard's LLM
menu are both DERIVED from this, so a provider can never exist in one view and
not the other — the drift tests/test_catalog.py used to pin by hand.

No default model is recorded. Model ids rot faster than anything else in this
codebase (the catalog this replaced still named gpt-4o-mini and llama-3.3-70b
in 2026), so the user names the model and the provider supplies nothing.

`base_url = None` means "whatever the openai SDK points at", i.e. api.openai.com.
That is correct for exactly one row. Any other provider left at None would post
THAT provider's key to OpenAI, so a test pins the set to {openai}.

`vision` marks providers whose OpenAI-compatible endpoint accepts `image_url`
content blocks. It gates only the AUTOMATIC fallback in Settings.vision_slot();
an explicit [vision] slot is honoured whatever this says. That asymmetry is why
the local servers are False: Ollama and LM Studio relay images fine, but
whether the loaded model can see is unknowable from here, and silently routing
a diagram to a text-only local model produces a confusing 400 rather than an
answer. Say no by default, and let someone who knows their setup say yes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMProvider:
    id: str
    label: str
    base_url: str | None       # None: the SDK's own default (OpenAI only)
    api_key_env: str           # "": provider needs no key (local servers)
    vision: bool = False       # endpoint accepts image_url content blocks
    native_adapter: str | None = None  # pipecat adapter module, if any
    note: str = ""


LLM_PROVIDERS: tuple[LLMProvider, ...] = (
    LLMProvider(
        id="groq", label="Groq", base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY", vision=True,
        note="fast open-weights inference; generous free tier",
    ),
    LLMProvider(
        id="openai", label="OpenAI", base_url=None,
        api_key_env="OPENAI_API_KEY", vision=True,
        note="GPT models",
    ),
    LLMProvider(
        id="anthropic", label="Anthropic",
        base_url="https://api.anthropic.com/v1/",
        api_key_env="ANTHROPIC_API_KEY", vision=True,
        native_adapter="pipecat.adapters.services.anthropic_adapter",
        note="Claude via the OpenAI-compat endpoint; no prompt caching there",
    ),
    LLMProvider(
        id="gemini", label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY", vision=True,
        native_adapter="pipecat.adapters.services.gemini_adapter",
        note="OpenAI-compat endpoint; the native adapter adds caching and thinking",
    ),
    LLMProvider(
        id="deepseek", label="DeepSeek", base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        note="deepseek-reasoner exposes reasoning_content for the deep slot",
    ),
    LLMProvider(
        id="cerebras", label="Cerebras", base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        note="fastest tokens/sec around",
    ),
    LLMProvider(
        id="openrouter", label="OpenRouter", base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY", vision=True,
        note="one key, many models",
    ),
    LLMProvider(
        id="huggingface", label="Hugging Face",
        base_url="https://router.huggingface.co/v1", api_key_env="HF_TOKEN",
        note="HF Inference Providers router",
    ),
    LLMProvider(
        id="ollama", label="Ollama (local)", base_url="http://localhost:11434/v1",
        api_key_env="",
        note="runs on your machine — `ollama pull <model>` first",
    ),
    LLMProvider(
        id="lmstudio", label="LM Studio (local)", base_url="http://localhost:1234/v1",
        api_key_env="",
        note="uses whatever model LM Studio has loaded",
    ),
)


def find_llm(provider_id: str) -> LLMProvider | None:
    for provider in LLM_PROVIDERS:
        if provider.id == provider_id:
            return provider
    return None
