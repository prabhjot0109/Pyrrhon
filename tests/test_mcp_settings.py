from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrrhon.config.settings import MCPServerConfig, load_settings


def test_mcp_server_config_requires_exactly_one_transport():
    assert MCPServerConfig(command="npx", args=["-y", "some-server"]).command == "npx"
    assert MCPServerConfig(url="http://localhost:8931/mcp").url is not None
    with pytest.raises(ValidationError):
        MCPServerConfig()  # neither
    with pytest.raises(ValidationError):
        MCPServerConfig(command="npx", url="http://x")  # both


def test_mcp_servers_and_fallbacks_load_from_toml(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        "[mcp_servers.docs]\n"
        'command = "npx"\n'
        'args = ["-y", "@example/docs-mcp"]\n'
        "\n"
        "[mcp_servers.web]\n"
        'url = "http://localhost:8931/mcp"\n'
        "\n"
        "[fallbacks]\n"
        'fast = ["groq", "cerebras", "openai"]\n',
        encoding="utf-8",
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.mcp_servers["docs"].command == "npx"
    assert settings.mcp_servers["docs"].args == ["-y", "@example/docs-mcp"]
    assert settings.mcp_servers["web"].url == "http://localhost:8931/mcp"
    assert settings.fallbacks["fast"] == ["groq", "cerebras", "openai"]


def test_defaults_are_empty(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.mcp_servers == {}
    assert settings.fallbacks == {}
