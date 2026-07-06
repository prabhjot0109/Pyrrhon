"""/settings shows what's configured and where keys come from — never key values."""

from pathlib import Path

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
