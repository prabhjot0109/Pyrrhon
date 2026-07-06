"""The catalog is the wizard's menu — it must stay in sync with the registries."""

from pyrrhon.config.catalog import LLM_CHOICES, STT_CHOICES, TTS_CHOICES
from pyrrhon.config.settings import BUILTIN_PROVIDERS
from pyrrhon.voice.providers import STT_PROVIDERS, TTS_PROVIDERS


def test_every_llm_choice_is_a_builtin_provider():
    assert {c.id for c in LLM_CHOICES} == set(BUILTIN_PROVIDERS)


def test_voice_choices_match_the_registries_exactly():
    assert {c.id for c in STT_CHOICES} == set(STT_PROVIDERS)
    assert {c.id for c in TTS_CHOICES} == set(TTS_PROVIDERS)


def test_keyless_choices_are_marked_keyless():
    keyless = {c.id for c in LLM_CHOICES if c.key_env is None}
    assert keyless == {"ollama", "lmstudio"}
    assert {c.id for c in STT_CHOICES if c.key_env is None} == {"whisper-local"}
    assert {c.id for c in TTS_CHOICES if c.key_env is None} == {"piper"}


def test_every_choice_has_a_label_and_note():
    for choice in (*LLM_CHOICES, *STT_CHOICES, *TTS_CHOICES):
        assert choice.label
        assert choice.note
