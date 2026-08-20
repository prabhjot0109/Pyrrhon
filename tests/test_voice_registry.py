"""The provider table: shape, uniqueness, and the invariants the factory relies on."""

from pyrrhon.voice.registry import VOICE_PROVIDERS, find, stt_providers, tts_providers


def test_ids_are_unique_within_each_kind():
    for kind in ("stt", "tts"):
        ids = [p.id for p in VOICE_PROVIDERS if p.kind == kind]
        assert len(ids) == len(set(ids)), f"duplicate {kind} id"


def test_keyless_providers_declare_no_key_env():
    for provider in VOICE_PROVIDERS:
        if provider.id in ("whisper-local", "moonshine", "piper", "kokoro"):
            assert provider.key_env is None, f"{provider.id} should be keyless"


def test_every_provider_names_a_pipecat_or_pyrrhon_module():
    for provider in VOICE_PROVIDERS:
        assert provider.module.startswith(("pipecat.", "pyrrhon.")), provider.id


def test_find_returns_none_for_unknown():
    assert find("tts", "definitely-not-a-provider") is None
    assert find("tts", "piper") is not None


def test_split_helpers_agree_with_the_table():
    assert set(stt_providers()) | set(tts_providers()) == set(VOICE_PROVIDERS)
