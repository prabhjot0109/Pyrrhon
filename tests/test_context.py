"""Token estimation + tool-result eviction (pure, no LLM)."""

from pyrrhon.core.context import (
    TOOL_STUB_MIN,
    compact_tool_results,
    estimate_tokens,
    history_tokens,
)


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
