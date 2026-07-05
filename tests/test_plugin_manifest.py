import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrrhon.plugins import parse_manifest


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "plugin.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_minimal_manifest_gets_all_defaults(tmp_path: Path):
    m = parse_manifest(write(tmp_path, 'name = "hello"\nversion = "0.1.0"\n'))
    assert (m.name, m.version, m.description) == ("hello", "0.1.0", "")
    assert m.contributes.prompts == []
    assert m.contributes.tools is None
    assert m.contributes.commands is None
    assert m.contributes.mcp_servers == {}
    assert m.contributes.providers == {}


def test_full_manifest_roundtrip(tmp_path: Path):
    m = parse_manifest(
        write(
            tmp_path,
            'name = "hello-reviewer"\n'
            'version = "0.1.0"\n'
            'description = "Review helper."\n'
            "\n"
            "[contributes]\n"
            'prompts = ["prompts/*.md"]\n'
            'tools = "tools.py:get_tools"\n'
            'commands = "commands.py:get_commands"\n'
            "\n"
            "[contributes.mcp_servers.docs]\n"
            'command = "docs-mcp"\n'
            "\n"
            "[contributes.providers.myllm]\n"
            'base_url = "http://localhost:8000/v1"\n'
            'api_key_env = "MYLLM_KEY"\n',
        )
    )
    assert m.contributes.prompts == ["prompts/*.md"]
    assert m.contributes.tools == "tools.py:get_tools"
    assert m.contributes.commands == "commands.py:get_commands"
    assert m.contributes.mcp_servers["docs"] == {"command": "docs-mcp"}
    assert m.contributes.providers["myllm"]["api_key_env"] == "MYLLM_KEY"


def test_missing_version_raises_validation_error(tmp_path: Path):
    with pytest.raises(ValidationError):
        parse_manifest(write(tmp_path, 'name = "x"\n'))


def test_bad_toml_raises_decode_error(tmp_path: Path):
    with pytest.raises(tomllib.TOMLDecodeError):
        parse_manifest(write(tmp_path, 'name = "unclosed\n'))


def test_path_traversal_name_rejected(tmp_path: Path):
    with pytest.raises(ValidationError):
        parse_manifest(write(tmp_path, 'name = "../evil"\nversion = "1"\n'))
