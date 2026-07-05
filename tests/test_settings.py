from pathlib import Path

import pytest

from pyrrhon.config.settings import ModelSlot, Settings, load_settings


def test_defaults_when_no_files(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.fast.provider == "groq"
    assert settings.deep is None
    assert settings.deep_slot == settings.fast  # unambiguous fallback rule


def test_repo_config_overrides_global(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".pyrrhon").mkdir(parents=True)
    (home / ".pyrrhon" / "config.toml").write_text(
        '[fast]\nprovider = "openai"\nmodel = "gpt-4.1-mini"\n', encoding="utf-8"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[fast]\nprovider = "cerebras"\nmodel = "llama3.3-70b"\n', encoding="utf-8"
    )
    settings = load_settings(repo_root=repo, home=home)
    assert settings.fast.provider == "cerebras"


def test_builtin_provider_lookup_and_unknown_raises(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    provider = settings.provider_for(settings.fast)
    assert provider.api_key_env == "GROQ_API_KEY"
    with pytest.raises(KeyError):
        settings.provider_for(ModelSlot(provider="doesnotexist", model="x"))


def test_custom_provider_in_config(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        "[providers.myproxy]\n"
        'base_url = "http://localhost:8000/v1"\n'
        'api_key_env = "MYPROXY_KEY"\n'
        "[fast]\n"
        'provider = "myproxy"\nmodel = "local-model"\n',
        encoding="utf-8",
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.provider_for(settings.fast).base_url == "http://localhost:8000/v1"


def test_context_settings_defaults_and_override(tmp_path: Path):
    assert load_settings(tmp_path).context.budget_tokens == 32000
    (tmp_path / ".pyrrhon.toml").write_text(
        "[context]\nbudget_tokens = 9000\nkeep_last_messages = 4\n", encoding="utf-8"
    )
    settings = load_settings(tmp_path)
    assert settings.context.budget_tokens == 9000
    assert settings.context.keep_last_messages == 4


def test_voice_settings_defaults_and_override(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.voice.tts_voice == "nova"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[voice]\ntts_voice = "onyx"\nchars_per_sec = 12.5\n', encoding="utf-8"
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.voice.tts_voice == "onyx"
    assert settings.voice.chars_per_sec == 12.5
