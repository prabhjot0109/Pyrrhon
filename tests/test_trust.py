"""Grants: content-bound consent records for anything a repo supplies."""

from pathlib import Path

from pyrrhon.config.trust import (
    Grant,
    digest_value,
    read_trust_file,
    record_grants,
)


def test_digest_is_stable_across_key_order():
    a = digest_value({"command": "node", "args": ["x.js"]})
    b = digest_value({"args": ["x.js"], "command": "node"})
    assert a == b


def test_digest_changes_when_the_value_changes():
    before = digest_value({"command": "node", "args": ["x.js"]})
    after = digest_value({"command": "node", "args": ["evil.js"]})
    assert before != after


def test_grant_line_round_trips(tmp_path: Path):
    grant = Grant(
        kind="config",
        key="mcp_servers.indexer",
        digest=digest_value({"command": "node"}),
        effect="run a program: node",
    )
    record_grants(tmp_path, [grant])
    assert read_trust_file(tmp_path).has(grant)


def test_a_changed_value_is_not_covered_by_the_old_grant(tmp_path: Path):
    granted = Grant("config", "mcp_servers.indexer", digest_value({"command": "node"}), "x")
    record_grants(tmp_path, [granted])
    tampered = Grant("config", "mcp_servers.indexer", digest_value({"command": "curl"}), "x")
    assert not read_trust_file(tmp_path).has(tampered)


def test_legacy_bare_plugin_names_still_load(tmp_path: Path):
    directory = tmp_path / ".pyrrhon"
    directory.mkdir()
    (directory / "trusted").write_text("hello-reviewer\n", encoding="utf-8")
    assert read_trust_file(tmp_path).plugins == {"hello-reviewer"}


def test_recording_is_idempotent(tmp_path: Path):
    grant = Grant("soul", ".pyrrhon/team.md", digest_value("hi"), "x")
    record_grants(tmp_path, [grant])
    record_grants(tmp_path, [grant])
    body = (tmp_path / ".pyrrhon" / "trusted").read_text(encoding="utf-8")
    assert body.count(grant.line) == 1


def test_grant_lines_are_not_mistaken_for_plugin_names(tmp_path: Path):
    """The plugin loader and the grant reader share one file. A grant line must
    never come back as a plugin the user agreed to execute."""
    from pyrrhon.plugins import read_trusted

    record_grants(tmp_path, [Grant("config", "mcp_servers.x", digest_value({}), "x")])
    (tmp_path / ".pyrrhon" / "trusted").open("a", encoding="utf-8").write(
        "hello-reviewer\n"
    )
    assert read_trusted(tmp_path) == {"hello-reviewer"}
