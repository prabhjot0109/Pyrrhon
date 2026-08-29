"""Setup wizard: scripted IO, no real terminal, keys never land in config.toml."""

import tomllib

from pyrrhon.config.catalog import llm_choices, stt_choices, tts_choices
from pyrrhon.config.credentials import read_credentials
from pyrrhon.config.wizard import needs_setup, run_wizard


def scripted(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def pick(choices, provider_id: str) -> str:
    """The menu answer that selects `provider_id`.

    The wizard picks by number and every menu is DERIVED from a provider
    table now, so a hard-coded index would break every time a row is added —
    which is a property of the table, not a regression.
    """
    return str(next(i for i, c in enumerate(choices, 1) if c.id == provider_id))


class QuietConsole:
    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))


def test_full_run_writes_config_and_credentials(tmp_path):
    console = QuietConsole()
    run_wizard(
        home=tmp_path,
        console=console,
        # LLM: gemini + an explicit model id (there is no default), then
        # voice: yes, STT: gemini, TTS: gemini, confirm summary.
        input_fn=scripted(
            pick(llm_choices(), "gemini"), "gemini-2.5-flash", "y",
            pick(stt_choices(), "gemini"),
            pick(tts_choices(), "gemini"),
            "y",
        ),
        getpass_fn=scripted("AIza-secret"),
    )
    config = tomllib.loads((tmp_path / ".pyrrhon" / "config.toml").read_text())
    assert config["fast"] == {"provider": "gemini", "model": "gemini-2.5-flash"}
    assert config["voice"]["stt_provider"] == "gemini"
    assert config["voice"]["tts_provider"] == "gemini"
    assert read_credentials(home=tmp_path) == {"GEMINI_API_KEY": "AIza-secret"}
    # The key value must never be echoed anywhere.
    assert not any("AIza-secret" in line for line in console.lines)


def test_skipping_voice_leaves_voice_section_alone(tmp_path):
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        # groq, model id, no voice, confirm
        input_fn=scripted(pick(llm_choices(), "groq"), "moonshotai/kimi-k2", "n", "y"),
        getpass_fn=scripted("gsk-abc"),
    )
    config = tomllib.loads((tmp_path / ".pyrrhon" / "config.toml").read_text())
    assert config["fast"] == {"provider": "groq", "model": "moonshotai/kimi-k2"}
    assert "voice" not in config


def test_existing_sections_survive_a_rerun(tmp_path):
    pyrrhon_dir = tmp_path / ".pyrrhon"
    pyrrhon_dir.mkdir()
    (pyrrhon_dir / "config.toml").write_text(
        '[mcp_servers.docs]\nurl = "http://localhost:9000"\n', encoding="utf-8"
    )
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        input_fn=scripted(pick(llm_choices(), "groq"), "openai/gpt-oss-120b", "n", "y"),
        getpass_fn=scripted("gsk-abc"),
    )
    config = tomllib.loads((pyrrhon_dir / "config.toml").read_text())
    assert config["mcp_servers"]["docs"]["url"] == "http://localhost:9000"
    assert config["fast"]["provider"] == "groq"


def test_keyless_provider_asks_for_no_key(tmp_path):
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        # ollama, model id, no voice, confirm
        input_fn=scripted(pick(llm_choices(), "ollama"), "qwen3", "n", "y"),
        getpass_fn=scripted(),                  # would raise StopIteration if called
    )
    assert read_credentials(home=tmp_path) == {}


def test_an_empty_llm_model_is_re_asked_rather_than_written(tmp_path):
    """No catalog default means the user must name a model.

    Accepting the empty answer would write `model = None`, which tomli_w
    refuses and ModelSlot could not validate — so the wizard insists instead.
    """
    console = QuietConsole()
    run_wizard(
        home=tmp_path,
        console=console,
        input_fn=scripted(
            pick(llm_choices(), "ollama"), "", "  ", "qwen3", "n", "y"
        ),
        getpass_fn=scripted(),
    )
    config = tomllib.loads((tmp_path / ".pyrrhon" / "config.toml").read_text())
    assert config["fast"] == {"provider": "ollama", "model": "qwen3"}
    assert sum("no default model" in line for line in console.lines) == 2


def test_needs_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert needs_setup(home=tmp_path) is True
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert needs_setup(home=tmp_path) is False
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    (tmp_path / ".pyrrhon").mkdir()
    (tmp_path / ".pyrrhon" / "config.toml").write_text("", encoding="utf-8")
    assert needs_setup(home=tmp_path) is False


def test_ensure_configured_loads_credentials_and_skips_wizard_when_configured(
    tmp_path, monkeypatch
):
    from pyrrhon.config.credentials import save_credentials
    from pyrrhon.config.wizard import ensure_configured

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (tmp_path / ".pyrrhon").mkdir()
    (tmp_path / ".pyrrhon" / "config.toml").write_text("", encoding="utf-8")
    save_credentials({"GEMINI_API_KEY": "gem"}, home=tmp_path)

    ensure_configured(home=tmp_path, ask=lambda prompt: (_ for _ in ()).throw(
        AssertionError("wizard offered despite existing config")))
    import os
    assert os.environ["GEMINI_API_KEY"] == "gem"


def test_ensure_configured_offers_wizard_on_first_run(tmp_path, monkeypatch):
    from pyrrhon.config import wizard as wizard_mod

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ran = {}
    monkeypatch.setattr(wizard_mod, "run_wizard", lambda home: ran.setdefault("home", home))
    wizard_mod.ensure_configured(home=tmp_path, ask=lambda prompt: "y")
    assert ran["home"] == tmp_path


def test_ensure_configured_respects_a_no(tmp_path, monkeypatch):
    from pyrrhon.config import wizard as wizard_mod

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(wizard_mod, "run_wizard",
                        lambda home: (_ for _ in ()).throw(AssertionError("ran anyway")))
    wizard_mod.ensure_configured(home=tmp_path, ask=lambda prompt: "n")


def test_switching_provider_drops_the_previous_provider_s_model_and_voice(tmp_path):
    """A rerun must not leave one provider's ids pointed at another provider.

    Piper's voice id reached Deepgram as its `model` query param and the
    handshake came back HTTP 400; Groq's whisper id reached Deepgram's
    listen socket and came back 405. Both survived because the old writer
    merged the previous [voice] table and only overwrote a detail key when
    the NEW provider happened to carry a catalog default — Deepgram carries
    neither, so the stale ids rode through the switch.
    """
    pyrrhon_dir = tmp_path / ".pyrrhon"
    pyrrhon_dir.mkdir()
    (pyrrhon_dir / "config.toml").write_text(
        "[voice]\n"
        'stt_provider = "groq"\n'
        'tts_provider = "piper"\n'
        'stt_model = "whisper-large-v3-turbo"\n'
        'tts_voice = "en_US-lessac-medium"\n'
        "chars_per_sec = 12.5\n",
        encoding="utf-8",
    )
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        input_fn=scripted(
            pick(llm_choices(), "groq"), "openai/gpt-oss-120b", "y",
            pick(stt_choices(), "deepgram"),
            pick(tts_choices(), "deepgram"),
            "y",
        ),
        getpass_fn=scripted("gsk-abc", "dg-abc"),
    )
    voice = tomllib.loads((pyrrhon_dir / "config.toml").read_text())["voice"]
    assert voice["stt_provider"] == "deepgram"
    assert voice["tts_provider"] == "deepgram"
    assert "stt_model" not in voice
    assert "tts_voice" not in voice
    # Knobs the wizard never asks about are not its to clear.
    assert voice["chars_per_sec"] == 12.5


def test_a_provider_with_a_default_voice_still_pins_it(tmp_path):
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        input_fn=scripted(
            pick(llm_choices(), "groq"), "openai/gpt-oss-120b", "y",
            pick(stt_choices(), "groq"),
            pick(tts_choices(), "piper"),
            "y",
        ),
        getpass_fn=scripted("gsk-abc"),
    )
    voice = tomllib.loads((tmp_path / ".pyrrhon" / "config.toml").read_text())["voice"]
    assert voice["tts_voice"] == "en_US-lessac-medium"
