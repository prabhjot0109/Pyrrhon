from pathlib import Path

from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

M4_TOOLS = {
    "find_symbol",
    # M14: symbol_context took find_references' place on the belt — same
    # `name` argument, its rows plus the source window. See tests/test_safety.py.
    "symbol_context",
    "git_log",
    "git_blame",
    "git_show",
    "web_search",
    "web_fetch",
}


def test_build_agent_registers_m4_tools_without_deep_key(monkeypatch, tmp_path):
    # home=tmp_path: isolate from the developer's real ~/.pyrrhon/config.toml —
    # it can point the fast slot at another provider entirely, in which case
    # GROQ_API_KEY is not the key this test thinks it is controlling.
    # Same fence tests/test_safety.py puts around the tool belt.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    agent = build_agent(FIXTURE, llm=FakeLLM([]), home=tmp_path)
    assert M4_TOOLS <= set(agent.tools)
    assert {"read_file", "grep", "glob"} <= set(agent.tools)  # M0 tools still there
    # deep_slot falls back to fast (groq); no key -> no escalation tool:
    assert "think_deeper" not in agent.tools


def test_build_agent_wires_deep_slot_when_key_present(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    agent = build_agent(FIXTURE, llm=FakeLLM([]), home=tmp_path)
    assert "think_deeper" in agent.tools


def test_build_agent_construction_does_no_index_io(monkeypatch, tmp_path):
    # SymbolIndex.__init__ is I/O-free, so building an agent must not create
    # .pyrrhon/cache.db — the index is built lazily on first tool use.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    build_agent(repo, llm=FakeLLM([]), home=tmp_path)
    assert not (repo / ".pyrrhon" / "cache.db").exists()


def test_build_agent_gives_the_subagent_a_read_only_belt(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    agent = build_agent(
        tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path
    )
    deep_tool = agent.tools["think_deeper"]
    belt = set(deep_tool.tools)
    assert {"read_file", "grep", "glob", "find_symbol", "symbol_context",
            "list_dependencies", "repo_map", "git_log", "git_blame",
            "git_show"} <= belt
    assert "think_deeper" not in belt
    assert "write_spec" not in belt
    assert "web_search" not in belt
