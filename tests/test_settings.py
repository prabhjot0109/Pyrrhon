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


CUSTOM_PROVIDER_TOML = (
    "[providers.myproxy]\n"
    'base_url = "http://localhost:8000/v1"\n'
    'api_key_env = "MYPROXY_KEY"\n'
    "[fast]\n"
    'provider = "myproxy"\nmodel = "local-model"\n'
)
_MYPROXY = {"base_url": "http://localhost:8000/v1", "api_key_env": "MYPROXY_KEY"}


def test_custom_provider_in_config(tmp_path: Path):
    """M11 changed this test's premise. A repo-defined provider plus a slot
    aimed at it IS the key-redirection attack, so it now needs a grant. The
    capability is unchanged once consent is on record — see the pair below."""
    from pyrrhon.config.trust import Grant, digest_value, record_grants

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(CUSTOM_PROVIDER_TOML, encoding="utf-8")
    record_grants(
        repo, [Grant("config", "providers.myproxy", digest_value(_MYPROXY), "x")]
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.provider_for(settings.fast).base_url == "http://localhost:8000/v1"


def test_an_ungranted_custom_provider_does_not_capture_the_fast_slot(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(CUSTOM_PROVIDER_TOML, encoding="utf-8")
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert "myproxy" not in settings.providers
    assert settings.fast.provider == "groq"  # the default, not the repo's choice
    assert {g.key for g in settings.pending_grants} == {"providers.myproxy", "fast"}


def test_voice_provider_settings_defaults_and_override(tmp_path: Path):
    # home= isolates the test from the developer's real ~/.pyrrhon/config.toml,
    # which otherwise overrides these defaults (matches test_custom_provider).
    nohome = tmp_path / "nohome"
    voice = load_settings(tmp_path, home=nohome).voice
    assert voice.stt_provider == "groq"
    assert voice.tts_provider == "openai"
    (tmp_path / ".pyrrhon.toml").write_text(
        '[voice]\ntts_provider = "cartesia"\ntts_voice = "some-voice-id"\n'
        'tts_model = "sonic-2"\nstt_provider = "whisper-local"\n',
        encoding="utf-8",
    )
    voice = load_settings(tmp_path, home=nohome).voice
    assert voice.tts_provider == "cartesia"
    assert voice.tts_model == "sonic-2"
    assert voice.stt_provider == "whisper-local"


def test_local_llm_providers_are_builtin_and_keyless():
    from pyrrhon.config.settings import BUILTIN_PROVIDERS

    assert BUILTIN_PROVIDERS["ollama"].base_url == "http://localhost:11434/v1"
    assert BUILTIN_PROVIDERS["ollama"].api_key_env == ""
    assert BUILTIN_PROVIDERS["lmstudio"].base_url == "http://localhost:1234/v1"
    # Hosted fast-inference providers stay available alongside the local ones.
    assert BUILTIN_PROVIDERS["cerebras"].base_url == "https://api.cerebras.ai/v1"
    assert BUILTIN_PROVIDERS["cerebras"].api_key_env == "CEREBRAS_API_KEY"


def test_deepseek_and_huggingface_are_builtin_providers():
    settings = Settings()
    deepseek = settings.provider_for(ModelSlot(provider="deepseek", model="deepseek-chat"))
    assert deepseek.base_url == "https://api.deepseek.com/v1"
    assert deepseek.api_key_env == "DEEPSEEK_API_KEY"
    hf = settings.provider_for(
        ModelSlot(provider="huggingface", model="meta-llama/Llama-3.3-70B-Instruct")
    )
    assert hf.base_url == "https://router.huggingface.co/v1"
    assert hf.api_key_env == "HF_TOKEN"


def test_context_settings_defaults_and_override(tmp_path: Path):
    # 90k since M10, up from 32k: most fast models carry a 128k window, and at
    # 32k compaction fired constantly — each firing is a full LLM round trip.
    assert load_settings(tmp_path).context.budget_tokens == 90000
    (tmp_path / ".pyrrhon.toml").write_text(
        "[context]\nbudget_tokens = 9000\nkeep_last_messages = 4\n", encoding="utf-8"
    )
    settings = load_settings(tmp_path)
    assert settings.context.budget_tokens == 9000
    assert settings.context.keep_last_messages == 4


def test_voice_settings_defaults_and_override(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.voice.tts_voice is None
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[voice]\ntts_voice = "onyx"\nchars_per_sec = 12.5\n', encoding="utf-8"
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.voice.tts_voice == "onyx"
    assert settings.voice.chars_per_sec == 12.5


# --- M11: repo config is untrusted input ------------------------------------

from pyrrhon.config.trust import Grant, digest_value, record_grants  # noqa: E402


def test_repo_voice_table_no_longer_deletes_global_voice_keys(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    (home / ".pyrrhon").mkdir(parents=True)
    repo.mkdir()
    (home / ".pyrrhon" / "config.toml").write_text(
        '[voice]\nstt_provider = "gemini"\ntts_voice = "alloy"\n', encoding="utf-8"
    )
    (repo / ".pyrrhon.toml").write_text(
        '[voice]\ntts_provider = "piper"\n', encoding="utf-8"
    )
    settings = load_settings(repo, home)
    assert settings.voice.stt_provider == "gemini"
    assert settings.voice.tts_voice == "alloy"
    assert settings.voice.tts_provider == "piper"


def test_an_ungranted_repo_mcp_server_never_reaches_settings(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[mcp_servers.hostile]\ncommand = "calc.exe"\n', encoding="utf-8"
    )
    settings = load_settings(repo, home)
    assert settings.mcp_servers == {}
    assert [g.key for g in settings.pending_grants] == ["mcp_servers.hostile"]


def test_a_granted_repo_mcp_server_reaches_settings(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[mcp_servers.indexer]\ncommand = "node"\n', encoding="utf-8"
    )
    record_grants(
        repo,
        [Grant("config", "mcp_servers.indexer", digest_value({"command": "node"}), "x")],
    )
    settings = load_settings(repo, home)
    assert settings.mcp_servers["indexer"].command == "node"
    assert settings.pending_grants == []


def test_a_globally_configured_mcp_server_needs_no_grant(tmp_path):
    """The user's own ~/.pyrrhon/config.toml is not untrusted input; only the
    repo's file is. Gating the global one would prompt users about themselves."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    (home / ".pyrrhon").mkdir(parents=True)
    repo.mkdir()
    (home / ".pyrrhon" / "config.toml").write_text(
        '[mcp_servers.mine]\ncommand = "node"\n', encoding="utf-8"
    )
    settings = load_settings(repo, home)
    assert settings.mcp_servers["mine"].command == "node"
    assert settings.pending_grants == []


def test_pending_grants_never_round_trip_into_a_config_file(tmp_path):
    """pending_grants is runtime state, not configuration. If it serialized,
    /settings and the wizard would write consent decisions into config.toml."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[mcp_servers.hostile]\ncommand = "calc.exe"\n', encoding="utf-8"
    )
    settings = load_settings(repo, home)
    assert settings.pending_grants  # there is something to exclude
    assert "pending_grants" not in settings.model_dump()
