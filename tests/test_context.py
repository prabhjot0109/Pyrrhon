"""Token estimation + tool-result eviction (pure, no LLM)."""

from pyrrhon.core.context import (
    TOOL_STUB_MIN,
    compact_tool_results,
    estimate_tokens,
    history_tokens,
    maybe_summarize,
)
from pyrrhon.core.providers.llm import LLMReply
from tests.helpers import FakeLLM


def test_estimate_tokens_chars_over_four():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 100) == 100
    assert estimate_tokens("abc") == 1  # short text still counts as >= 1


def test_history_tokens_counts_content_and_tool_calls():
    history = [
        {"role": "system", "content": "x" * 400},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "grep", "arguments": '{"pattern": "foo"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "y" * 400},
    ]
    total = history_tokens(history)
    assert total >= 200  # 100 (system) + 100 (tool) + something for the call


def _history_with_big_tool_result() -> list[dict]:
    return [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "grep", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "match\n" * 500},
        {"role": "assistant", "content": "answer citing app.py:5"},
        {"role": "user", "content": "second question"},
    ]


def test_compact_elides_tool_results_from_earlier_turns():
    history = _history_with_big_tool_result()
    elided = compact_tool_results(history)
    assert elided == 1
    tool_msg = history[3]
    assert len(tool_msg["content"]) < TOOL_STUB_MIN
    assert tool_msg["content"].startswith("match")          # head preserved
    assert "re-run the tool" in tool_msg["content"]         # stub marker
    assert tool_msg["tool_call_id"] == "c1"                 # API contract intact


def test_compact_spares_current_turn_and_small_results():
    history = _history_with_big_tool_result()
    # Move the big result into the CURRENT turn: last user msg comes first.
    history = history[:1] + [history[5]] + history[2:5]
    assert compact_tool_results(history) == 0

    small = [
        {"role": "system", "content": "p"},
        {"role": "user", "content": "q1"},
        {"role": "tool", "tool_call_id": "c1", "content": "short"},
        {"role": "user", "content": "q2"},
    ]
    assert compact_tool_results(small) == 0
    assert small[2]["content"] == "short"


def test_compact_is_idempotent():
    history = _history_with_big_tool_result()
    compact_tool_results(history)
    once = history[3]["content"]
    compact_tool_results(history)
    assert history[3]["content"] == once


def _long_history(n_turns: int = 6) -> list[dict]:
    history = [{"role": "system", "content": "base prompt"}]
    for i in range(n_turns):
        history.append({"role": "user", "content": f"question {i} " + "pad " * 200})
        history.append(
            {"role": "assistant", "content": f"answer {i} cites app.py:{i} " + "pad " * 200}
        )
    return history


async def test_summarize_noop_under_budget():
    history = _long_history(1)
    llm = FakeLLM([])  # would raise if called
    assert await maybe_summarize(history, llm, budget_tokens=100_000) is False


async def test_summarize_replaces_middle_and_keeps_tail_and_system():
    history = _long_history(6)
    tail_before = [dict(m) for m in history[-4:]]
    llm = FakeLLM([LLMReply(text="User explored the app. Key: app.py:3 handles greeting.")])
    assert await maybe_summarize(history, llm, budget_tokens=100, keep_last=4) is True
    assert history[0]["content"] == "base prompt"           # base prompt untouched
    assert history[1]["role"] == "system"                   # summary is a system msg
    assert "app.py:3" in history[1]["content"]
    assert history[-4:] == tail_before                      # recent turns verbatim
    assert len(history) == 2 + 4
    # The summarizer call itself must not offer tools.
    assert llm.calls[0]["tools"] is None


async def test_summarize_preserves_mode_system_messages():
    history = _long_history(6)
    history.insert(3, {"role": "system", "content": "DESIGN MODE POLICY"})
    llm = FakeLLM([LLMReply(text="summary text")])
    await maybe_summarize(history, llm, budget_tokens=100, keep_last=4)
    contents = [m["content"] for m in history if m["role"] == "system"]
    assert "DESIGN MODE POLICY" in contents                 # mode prompt survives


async def test_summarize_never_strands_tool_messages_at_tail_start():
    history = _long_history(4)
    history.extend([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c9", "type": "function",
             "function": {"name": "grep", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c9", "content": "hit"},
        {"role": "assistant", "content": "final"},
    ])
    llm = FakeLLM([LLMReply(text="summary")])
    await maybe_summarize(history, llm, budget_tokens=100, keep_last=2)
    roles = [m["role"] for m in history]
    first_tool = roles.index("tool")
    # The tool result's parent assistant tool_calls message must precede it.
    assert history[first_tool - 1].get("tool_calls")


async def test_summarize_failure_leaves_history_untouched():
    class ExplodingLLM:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    history = _long_history(6)
    before = [dict(m) for m in history]
    assert await maybe_summarize(history, ExplodingLLM(), budget_tokens=100) is False
    assert history == before
