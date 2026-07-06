"""Setup wizard: scripted IO, no real terminal, keys never land in config.toml."""

import tomllib

from pyrrhon.config.credentials import read_credentials
from pyrrhon.config.wizard import needs_setup, run_wizard


def scripted(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


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
        # LLM: pick 3 (gemini), accept default model, then voice: yes,
        # STT: pick 3 (gemini), TTS: pick 2 (gemini), confirm summary.
        input_fn=scripted("3", "", "y", "3", "2", "y"),
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
        input_fn=scripted("1", "", "n", "y"),   # groq, default model, no voice, confirm
        getpass_fn=scripted("gsk-abc"),
    )
    config = tomllib.loads((tmp_path / ".pyrrhon" / "config.toml").read_text())
    assert config["fast"]["provider"] == "groq"
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
        input_fn=scripted("1", "", "n", "y"),
        getpass_fn=scripted("gsk-abc"),
    )
    config = tomllib.loads((pyrrhon_dir / "config.toml").read_text())
    assert config["mcp_servers"]["docs"]["url"] == "http://localhost:9000"
    assert config["fast"]["provider"] == "groq"


def test_keyless_provider_asks_for_no_key(tmp_path):
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        input_fn=scripted("8", "", "n", "y"),   # ollama, default model, no voice, confirm
        getpass_fn=scripted(),                  # would raise StopIteration if called
    )
    assert read_credentials(home=tmp_path) == {}


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
