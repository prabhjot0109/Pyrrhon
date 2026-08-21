"""The LLM provider table and the two views derived from it.

Mirrors tests/test_voice_registry.py: the table is the single source of truth,
and anything that renders a provider list must be derived from it rather than
hand-maintained beside it.
"""

from pyrrhon.config.catalog import llm_choices
from pyrrhon.config.settings import BUILTIN_PROVIDERS
from pyrrhon.core.providers.registry import LLM_PROVIDERS, find_llm


def test_ids_are_unique():
    ids = [p.id for p in LLM_PROVIDERS]
    assert len(ids) == len(set(ids))


def test_every_row_has_a_label_and_a_note():
    for provider in LLM_PROVIDERS:
        assert provider.label, provider.id
        assert provider.note, provider.id


def test_no_provider_carries_a_hardcoded_model():
    """Same rule as the voice table: a default we do not set cannot go stale."""
    for provider in LLM_PROVIDERS:
        assert not hasattr(provider, "default_model")


def test_local_providers_need_no_key():
    for pid in ("ollama", "lmstudio"):
        provider = find_llm(pid)
        assert provider is not None and provider.api_key_env == ""


def test_hosted_providers_all_name_a_key_env():
    """A hosted endpoint with api_key_env='' would send 'local' as the key and
    fail with an opaque 401 instead of MissingAPIKeyError's actionable line."""
    for provider in LLM_PROVIDERS:
        if provider.base_url and provider.base_url.startswith("http://localhost"):
            continue
        assert provider.api_key_env, provider.id


def test_only_openai_itself_inherits_the_sdk_default_base_url():
    """base_url=None means 'whatever the openai SDK points at' — api.openai.com.

    Any other provider left at None would post that provider's key to OpenAI.
    """
    assert {p.id for p in LLM_PROVIDERS if p.base_url is None} == {"openai"}


def test_find_llm_is_none_for_an_unknown_id():
    assert find_llm("nope") is None


def test_builtin_providers_is_derived_from_the_table():
    assert set(BUILTIN_PROVIDERS) == {p.id for p in LLM_PROVIDERS}


def test_builtin_provider_config_matches_its_row():
    for provider in LLM_PROVIDERS:
        config = BUILTIN_PROVIDERS[provider.id]
        assert config.base_url == provider.base_url
        assert config.api_key_env == provider.api_key_env


def test_catalog_is_derived_from_the_table():
    assert {c.id for c in llm_choices()} == {p.id for p in LLM_PROVIDERS}


def test_vision_capable_providers_are_marked():
    assert find_llm("openai").vision is True
    assert find_llm("groq").vision is True
    assert find_llm("anthropic").vision is True
    assert find_llm("ollama").vision is False


def test_native_adapters_name_a_module_that_exists_in_pipecat():
    """A typo'd adapter path would only surface when someone selects that
    provider. Verified by locating the module, never by importing it — core/
    must stay importable without pipecat installed."""
    import importlib.util

    for provider in LLM_PROVIDERS:
        if provider.native_adapter is None:
            continue
        assert importlib.util.find_spec(provider.native_adapter) is not None, (
            f"{provider.id}: {provider.native_adapter}"
        )
