"""Credentials store: 0600 file, env-always-wins, values never in config.toml."""

import os
import stat

from pyrrhon.config.credentials import (
    credentials_path,
    load_credentials,
    read_credentials,
    save_credentials,
)


def test_roundtrip_and_merge(tmp_path):
    save_credentials({"GROQ_API_KEY": "gsk_1"}, home=tmp_path)
    save_credentials({"GEMINI_API_KEY": "AIza_2"}, home=tmp_path)
    assert read_credentials(home=tmp_path) == {
        "GROQ_API_KEY": "gsk_1",
        "GEMINI_API_KEY": "AIza_2",
    }


def test_values_with_quotes_and_backslashes_survive(tmp_path):
    tricky = 'ab"c\\d'
    save_credentials({"X_KEY": tricky}, home=tmp_path)
    assert read_credentials(home=tmp_path)["X_KEY"] == tricky


def test_missing_file_reads_empty(tmp_path):
    assert read_credentials(home=tmp_path) == {}


def test_load_sets_env_but_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    save_credentials(
        {"GROQ_API_KEY": "from-file", "GEMINI_API_KEY": "gem-file"}, home=tmp_path
    )
    load_credentials(home=tmp_path)
    assert os.environ["GROQ_API_KEY"] == "from-env"      # env always wins
    assert os.environ["GEMINI_API_KEY"] == "gem-file"    # file fills the gap


def test_file_is_owner_only_on_posix(tmp_path):
    save_credentials({"GROQ_API_KEY": "k"}, home=tmp_path)
    if os.name != "posix":
        return  # chmod is best-effort on Windows; nothing to assert
    mode = stat.S_IMODE(credentials_path(home=tmp_path).stat().st_mode)
    assert mode == 0o600
