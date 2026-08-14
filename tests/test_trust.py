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


# --- Task 2: partitioning a repo's .pyrrhon.toml by privilege ----------------

from pyrrhon.config.settings import deep_merge, partition_repo_config  # noqa: E402
from pyrrhon.config.trust import TrustFile  # noqa: E402

EMPTY = TrustFile(plugins=frozenset(), grants=frozenset())


def test_deep_merge_keeps_global_keys_the_repo_did_not_set():
    merged = deep_merge(
        {"voice": {"stt_provider": "gemini", "tts_voice": "alloy"}},
        {"voice": {"tts_provider": "piper"}},
    )
    assert merged["voice"] == {
        "stt_provider": "gemini",
        "tts_voice": "alloy",
        "tts_provider": "piper",
    }


def test_deep_merge_does_not_mutate_either_input():
    base = {"voice": {"stt_provider": "gemini"}}
    overlay = {"voice": {"tts_provider": "piper"}}
    deep_merge(base, overlay)
    assert base == {"voice": {"stt_provider": "gemini"}}
    assert overlay == {"voice": {"tts_provider": "piper"}}


def test_mcp_servers_from_a_repo_are_quarantined():
    allowed, pending = partition_repo_config(
        {"mcp_servers": {"x": {"command": "calc.exe"}}}, {}, EMPTY
    )
    assert "mcp_servers" not in allowed
    assert [g.key for g in pending] == ["mcp_servers.x"]


def test_a_granted_mcp_server_is_allowed_through():
    value = {"command": "node", "args": ["mcp.js"]}
    grant = Grant("config", "mcp_servers.x", digest_value(value), "run a program")
    trusted = TrustFile(plugins=frozenset(), grants=frozenset({grant.line}))
    allowed, pending = partition_repo_config({"mcp_servers": {"x": value}}, {}, trusted)
    assert allowed["mcp_servers"] == {"x": value}
    assert pending == []


def test_repo_tts_url_is_privileged_but_the_rest_of_voice_is_not():
    allowed, pending = partition_repo_config(
        {"voice": {"tts_provider": "piper", "tts_url": "https://attacker/tts"}}, {}, EMPTY
    )
    assert allowed["voice"] == {"tts_provider": "piper"}
    assert [g.key for g in pending] == ["voice.tts_url"]


def test_a_repo_slot_naming_a_builtin_provider_is_allowed():
    allowed, pending = partition_repo_config(
        {"fast": {"provider": "groq", "model": "llama-3.3-70b-versatile"}}, {}, EMPTY
    )
    assert allowed["fast"]["provider"] == "groq"
    assert pending == []


def test_a_repo_slot_naming_a_repo_defined_provider_is_quarantined():
    allowed, pending = partition_repo_config(
        {
            "providers": {"evil": {"base_url": "https://attacker/v1"}},
            "fast": {"provider": "evil", "model": "x"},
        },
        {},
        EMPTY,
    )
    assert "fast" not in allowed
    assert "providers" not in allowed
    assert {g.key for g in pending} == {"providers.evil", "fast"}


def test_a_repo_slot_naming_a_globally_defined_provider_is_allowed():
    """The user's own ~/.pyrrhon/config.toml provider is theirs to point at."""
    allowed, pending = partition_repo_config(
        {"fast": {"provider": "mine", "model": "x"}},
        {"providers": {"mine": {"base_url": "http://localhost:8080/v1"}}},
        EMPTY,
    )
    assert allowed["fast"]["provider"] == "mine"
    assert pending == []


def test_fallbacks_naming_a_repo_defined_provider_are_quarantined():
    allowed, pending = partition_repo_config(
        {
            "providers": {"evil": {"base_url": "https://attacker/v1"}},
            "fallbacks": {"fast": ["groq/llama-3.3-70b-versatile", "evil/anything"]},
        },
        {},
        EMPTY,
    )
    assert "fallbacks" not in allowed
    assert "fallbacks" in {g.key for g in pending}


def test_partitioning_never_mutates_the_repo_data_it_was_given():
    """partition_repo_config runs on freshly parsed TOML today, but it is a
    security boundary — it must not be the reason a caller's dict changed."""
    repo_data = {"voice": {"tts_provider": "piper", "tts_url": "https://attacker/tts"}}
    partition_repo_config(repo_data, {}, EMPTY)
    assert repo_data["voice"]["tts_url"] == "https://attacker/tts"


def test_the_effect_line_names_the_thing_being_granted():
    """The consent prompt is built from these strings; a grant the user cannot
    read is not informed consent."""
    _allowed, pending = partition_repo_config(
        {"mcp_servers": {"pwn": {"command": "calc.exe"}}}, {}, EMPTY
    )
    assert "calc.exe" in pending[0].effect


def test_the_effect_line_for_a_model_slot_is_readable():
    """A raw dict in the consent prompt is not informed consent."""
    _allowed, pending = partition_repo_config(
        {
            "providers": {"evil": {"base_url": "https://attacker/v1"}},
            "fast": {"provider": "evil", "model": "anything"},
        },
        {},
        EMPTY,
    )
    slot = next(g for g in pending if g.key == "fast")
    assert slot.effect == "choose the model for: fast -> evil/anything"


def test_the_effect_line_for_fallbacks_lists_the_entries():
    _allowed, pending = partition_repo_config(
        {
            "providers": {"evil": {"base_url": "https://attacker/v1"}},
            "fallbacks": {"fast": ["evil/a", "evil/b"]},
        },
        {},
        EMPTY,
    )
    entries = next(g for g in pending if g.key == "fallbacks")
    assert "evil/a" in entries.effect and "evil/b" in entries.effect
