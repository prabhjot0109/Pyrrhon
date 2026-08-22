"""The catalog is the wizard's menu — it must stay in sync with the registries."""

from pyrrhon.config.catalog import availability, llm_choices, stt_choices, tts_choices
from pyrrhon.config.settings import BUILTIN_PROVIDERS
from pyrrhon.voice.registry import find, stt_providers, tts_providers


def test_every_llm_choice_is_a_builtin_provider():
    assert {c.id for c in llm_choices()} == set(BUILTIN_PROVIDERS)


def test_no_llm_choice_pins_a_model():
    """Both views come off one table, and that table records no model id."""
    assert all(c.default_model is None for c in llm_choices())


def test_voice_choices_are_derived_from_the_registry():
    assert {c.id for c in stt_choices()} == {p.id for p in stt_providers()}
    assert {c.id for c in tts_choices()} == {p.id for p in tts_providers()}


def test_keyless_choices_are_marked_keyless():
    keyless = {c.id for c in llm_choices() if c.key_env is None}
    assert keyless == {"ollama", "lmstudio"}
    assert {c.id for c in stt_choices() if c.key_env is None} == {
        "whisper-local",
        "moonshine",
    }
    assert {c.id for c in tts_choices() if c.key_env is None} == {"piper", "kokoro"}


def test_every_choice_has_a_label_and_note():
    for choice in (*llm_choices(), *stt_choices(), *tts_choices()):
        assert choice.label
        assert choice.note


# -- availability: Pyrrhon may offer what it cannot run, never imply it can ---


def test_keyless_installed_smoke_tested_provider_is_ready(monkeypatch):
    piper = find("tts", "piper")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: True)
    assert piper.verified, "piper is the keyless default; it has to be tier 3 green"
    assert availability(piper) == "ready"


def test_a_row_no_tier_3_run_has_touched_says_unverified(monkeypatch):
    """The spec's answer to curating 20 rows we cannot all live-test.

    Installed and keyed is not the same as known to work: only tier 3 catches a
    retired model id, so a row without one says so rather than borrowing the
    confidence of the rows that have one.
    """
    inworld = find("tts", "inworld")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: True)
    monkeypatch.setattr("pyrrhon.config.catalog._dependencies_present", lambda m: True)
    monkeypatch.setenv("INWORLD_API_KEY", "k")
    assert not inworld.verified
    assert availability(inworld) == "ready, unverified"


def test_verified_is_never_claimed_for_a_row_that_cannot_run(monkeypatch):
    """Install state outranks it: a smoke test that passed on someone else's
    machine says nothing about whether the extra is present on this one."""
    groq = find("tts", "groq")
    assert groq.verified
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: False)
    assert availability(groq).startswith("install:")


def test_uninstalled_provider_names_the_command(monkeypatch):
    deepgram = find("tts", "deepgram")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: False)
    assert availability(deepgram) == 'install: uv add "pipecat-ai[deepgram]"'


def test_installed_but_keyless_provider_reports_the_missing_key(monkeypatch):
    cartesia = find("tts", "cartesia")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: True)
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    assert availability(cartesia) == "needs CARTESIA_API_KEY"


def test_a_present_module_with_an_unsatisfied_extra_is_not_ready(monkeypatch):
    """pipecat ships every service module in the base wheel.

    So find_spec succeeds for a provider whose third-party dependencies are
    absent, and reporting that as 'ready' is precisely the lie availability()
    exists to prevent.
    """
    kokoro = find("tts", "kokoro")
    monkeypatch.setattr("pyrrhon.config.catalog._installed", lambda module: True)
    monkeypatch.setattr("pyrrhon.config.catalog._dependencies_present", lambda m: False)
    assert availability(kokoro) == 'install: uv add "pipecat-ai[kokoro]"'


def test_runnability_is_read_from_what_the_module_itself_imports():
    """Not from what pipecat says the extra pulls in — an extra is coarser than
    a row. `pipecat-ai[deepgram]` covers a TTS service that is plain HTTP and an
    STT service that needs the vendor SDK, and the metadata question marked the
    TTS one uninstallable while tier 3 was making it speak.
    """
    from pyrrhon.config.catalog import _dependencies_present

    assert _dependencies_present("pipecat.services.piper.tts") is True
    assert _dependencies_present("pipecat.services.deepgram.tts") is True
    assert _dependencies_present("pipecat.services.deepgram.stt") is False
    assert _dependencies_present("pipecat.services.nowhere.at_all") is False


def test_a_namespace_package_is_not_mistaken_for_an_installed_one():
    """The trap that would have reintroduced the exact lie availability()
    prevents: `google` is a namespace package, so find_spec("google") succeeds
    on a machine with nothing under it. Gemini TTS imports `google.api_core`,
    and only the full dotted path tells the truth about it.
    """
    from pyrrhon.config.catalog import _dependencies_present, _toplevel_imports

    source = "from google.api_core import x\nimport os\nimport pipecat.frames\n"
    assert _toplevel_imports(source) == {"google.api_core"}, (
        "root-only would have said {'google'}, and stdlib/pipecat must drop out"
    )
    assert _dependencies_present("pipecat.services.google.tts") is False
