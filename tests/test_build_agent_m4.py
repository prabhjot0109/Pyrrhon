from pathlib import Path

from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

M4_TOOLS = {
    "find_symbol",
    "find_references",
    "git_log",
    "git_blame",
    "git_show",
    "web_search",
    "web_fetch",
}


def test_build_agent_registers_m4_tools_without_deep_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    assert M4_TOOLS <= set(agent.tools)
    assert {"read_file", "grep", "glob"} <= set(agent.tools)  # M0 tools still there
    # deep_slot falls back to fast (groq); no key -> no escalation tool:
    assert "think_deeper" not in agent.tools


def test_build_agent_wires_deep_slot_when_key_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    assert "think_deeper" in agent.tools


def test_build_agent_construction_does_no_index_io(monkeypatch, tmp_path):
    # SymbolIndex.__init__ is I/O-free, so building an agent must not create
    # .pyrrhon/cache.db — the index is built lazily on first tool use.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    build_agent(repo, llm=FakeLLM([]))
    assert not (repo / ".pyrrhon" / "cache.db").exists()
