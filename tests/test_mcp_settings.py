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
    # M11: a repo's MCP servers spawn processes, so they need a recorded grant
    # before load_settings applies them. [fallbacks] here names only builtin
    # providers, so it stays ungated — that split is the point of the design.
    from pyrrhon.config.trust import Grant, digest_value, record_grants

    record_grants(
        repo,
        [
            Grant(
                "config", "mcp_servers.docs",
                digest_value({"command": "npx", "args": ["-y", "@example/docs-mcp"]}),
                "x",
            ),
            Grant(
                "config", "mcp_servers.web",
                digest_value({"url": "http://localhost:8931/mcp"}), "x",
            ),
        ],
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.mcp_servers["docs"].command == "npx"
    assert settings.mcp_servers["docs"].args == ["-y", "@example/docs-mcp"]
    assert settings.mcp_servers["web"].url == "http://localhost:8931/mcp"
    assert settings.fallbacks["fast"] == ["groq", "cerebras", "openai"]
    assert settings.pending_grants == []


def test_fallbacks_over_builtin_providers_need_no_grant(tmp_path: Path):
    """A repo suggesting `groq, cerebras` routes nothing anywhere new — every
    name resolves to a builtin base_url and the user's own key."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        "[fallbacks]\nfast = [\"groq\", \"cerebras\"]\n", encoding="utf-8"
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.fallbacks["fast"] == ["groq", "cerebras"]
    assert settings.pending_grants == []


def test_defaults_are_empty(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.mcp_servers == {}
    assert settings.fallbacks == {}
