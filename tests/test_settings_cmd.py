"""/settings shows what's configured and where keys come from — never key values."""

import os

import pytest

from pyrrhon.commands import settings_cmd  # noqa: F401 — registers /settings
from pyrrhon.commands.registry import CommandContext, dispatch


class DummyUI:
    def notify(self, text: str) -> None: ...


@pytest.fixture
def ctx(tmp_path):
    return CommandContext(repo_root=tmp_path, agent=None, ui=DummyUI())


async def test_settings_lists_slots_and_key_status(ctx, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret_value")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Isolate from the developer's real ~/.pyrrhon/credentials.toml — a
    # stored key there would flip the "missing" assertion below.
    monkeypatch.setattr("pyrrhon.commands.settings_cmd.read_credentials", lambda: {})
    out = await dispatch("/settings", ctx)
    assert "groq" in out                      # default fast slot provider
    assert "GROQ_API_KEY" in out and "env" in out
    assert "GEMINI_API_KEY" in out and "missing" in out
    assert "gsk_secret_value" not in out      # value never rendered
    assert "--setup" in out                   # points at the wizard


async def test_settings_key_stores_masked_and_activates(ctx, monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(
        "pyrrhon.commands.settings_cmd.save_credentials",
        lambda updates: saved.update(updates),
    )
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    out = await dispatch("/settings key DEEPGRAM_API_KEY dgk_supersecret_1234", ctx)

    assert saved == {"DEEPGRAM_API_KEY": "dgk_supersecret_1234"}   # stored
    assert os.environ["DEEPGRAM_API_KEY"] == "dgk_supersecret_1234"  # active now
    assert "dgk_supersecret_1234" not in out                      # never echoed raw
    assert "dgk" in out and "1234" in out                         # masked fingerprint


async def test_settings_tts_persists_and_flags_missing_key(ctx, monkeypatch):
    patches: list = []
    monkeypatch.setattr(
        "pyrrhon.commands.settings_cmd.patch_config",
        lambda updates, **kw: patches.append(updates),
    )
    monkeypatch.setattr("pyrrhon.commands.settings_cmd.read_credentials", lambda: {})
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = await dispatch("/settings tts gemini", ctx)

    assert patches == [{"voice": {"tts_provider": "gemini", "tts_voice": "Kore"}}]
    assert "GEMINI_API_KEY" in out  # tells the user what key to add next


async def test_settings_llm_unknown_provider_errors_without_saving(ctx, monkeypatch):
    patches: list = []
    monkeypatch.setattr(
        "pyrrhon.commands.settings_cmd.patch_config",
        lambda updates, **kw: patches.append(updates),
    )
    out = await dispatch("/settings llm fast bogusprovider/some-model", ctx)
    assert out.startswith("ERROR")
    assert patches == []  # a typo must not persist


def test_redact_secret_echo_masks_only_the_key():
    from pyrrhon.tui.app import _redact_secret_echo

    assert (
        _redact_secret_echo("/settings key OPENAI_API_KEY sk-abc123")
        == "/settings key OPENAI_API_KEY ****"
    )
    # Ordinary input is untouched.
    assert _redact_secret_echo("what does run_turn do?") == "what does run_turn do?"
    assert _redact_secret_echo("/settings tts gemini") == "/settings tts gemini"
