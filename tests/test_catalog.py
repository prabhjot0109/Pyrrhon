"""The catalog is the wizard's menu — it must stay in sync with the registries."""

from pyrrhon.config.catalog import LLM_CHOICES, availability, stt_choices, tts_choices
from pyrrhon.config.settings import BUILTIN_PROVIDERS
from pyrrhon.voice.registry import find, stt_providers, tts_providers


def test_every_llm_choice_is_a_builtin_provider():
    assert {c.id for c in LLM_CHOICES} == set(BUILTIN_PROVIDERS)


def test_voice_choices_are_derived_from_the_registry():
    assert {c.id for c in stt_choices()} == {p.id for p in stt_providers()}
    assert {c.id for c in tts_choices()} == {p.id for p in tts_providers()}


def test_keyless_choices_are_marked_keyless():
    keyless = {c.id for c in LLM_CHOICES if c.key_env is None}
    assert keyless == {"ollama", "lmstudio"}
    assert {c.id for c in stt_choices() if c.key_env is None} == {
        "whisper-local",
        "moonshine",
    }
    assert {c.id for c in tts_choices() if c.key_env is None} == {"piper", "kokoro"}


def test_every_choice_has_a_label_and_note():
    for choice in (*LLM_CHOICES, *stt_choices(), *tts_choices()):
        assert choice.label
        assert choice.note


# -- availability: Pyrrhon may offer what it cannot run, never imply it can ---


def test_keyless_installed_provider_is_ready(monkeypatch):
    piper = find("tts", "piper")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: True)
    assert availability(piper) == "ready"


def test_uninstalled_provider_names_the_command(monkeypatch):
    deepgram = find("tts", "deepgram")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: False)
    assert availability(deepgram) == 'install: uv add "pipecat-ai[deepgram]"'


def test_installed_but_keyless_provider_reports_the_missing_key(monkeypatch):
    cartesia = find("tts", "cartesia")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: True)
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    assert availability(cartesia) == "needs CARTESIA_API_KEY"
