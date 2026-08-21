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


# -- calibrating the estimate against the provider's exact count -------------


def test_history_tokens_scales_the_estimate():
    history = [{"role": "user", "content": "x" * 4000}]

    assert history_tokens(history) == 1000            # len // 4
    assert history_tokens(history, scale=1.5) == 1500  # this model runs denser


def test_token_scale_from_usage_is_the_ratio_the_provider_reported():
    from pyrrhon.core.context import token_scale
    from pyrrhon.core.providers.llm import TokenUsage

    usage = TokenUsage(prompt=150, completion=9, total=159)
    assert token_scale(usage, estimated=100) == 1.5


def test_token_scale_ignores_a_provider_that_reports_nothing_useful():
    """No usage, a zero count, or an empty history must leave the scale alone —
    a bogus ratio would silently mis-budget every later turn."""
    from pyrrhon.core.context import token_scale
    from pyrrhon.core.providers.llm import TokenUsage

    assert token_scale(None, estimated=100) is None
    assert token_scale(TokenUsage(prompt=0, completion=1, total=1), 100) is None
    assert token_scale(TokenUsage(prompt=50, completion=1, total=51), 0) is None


def test_token_scale_is_clamped_to_a_believable_range():
    """len//4 is never off by 10x. A ratio that says it is means the provider
    counted something else (a cached prefix, a different request), and trusting
    it would either disable compaction or run it every turn."""
    from pyrrhon.core.context import MAX_TOKEN_SCALE, MIN_TOKEN_SCALE, token_scale
    from pyrrhon.core.providers.llm import TokenUsage

    assert token_scale(TokenUsage(prompt=10_000, completion=1, total=1), 100) == (
        MAX_TOKEN_SCALE
    )
    assert token_scale(TokenUsage(prompt=1, completion=1, total=1), 10_000) == (
        MIN_TOKEN_SCALE
    )


async def test_maybe_summarize_uses_the_scale_to_decide_it_is_over_budget():
    """Under the raw estimate this history fits; scaled, it does not."""
    history = [
        {"role": "system", "content": "prompt"},
        *[{"role": "user", "content": "x" * 400} for _ in range(10)],
    ]
    llm = FakeLLM([LLMReply(text="a summary")])

    assert await maybe_summarize(list(history), llm, budget_tokens=1200) is False
    assert await maybe_summarize(history, llm, budget_tokens=1200, scale=2.0) is True


async def test_the_agent_learns_its_token_scale_from_a_reply(tmp_path):
    """Whole-loop check: a provider that reports usage moves the scale."""
    from pyrrhon.bootstrap import build_agent
    from pyrrhon.core.providers.llm import TokenUsage

    llm = FakeLLM([LLMReply(text="ok", usage=TokenUsage(150, 5, 155))])
    agent = build_agent(tmp_path, llm=llm, deep_llm=None, home=tmp_path)
    assert agent.token_scale == 1.0

    history = [{"role": "system", "content": "x" * 400}]
    async for _ in agent.run_turn(history, "hello"):
        pass

    # The system prompt build_agent installs dominates, so assert the
    # direction and the bound rather than a brittle exact ratio.
    assert 0.5 <= agent.token_scale <= 2.0
    assert agent.token_scale != 1.0


async def test_a_provider_that_omits_usage_leaves_the_scale_alone(tmp_path):
    from pyrrhon.bootstrap import build_agent

    agent = build_agent(
        tmp_path, llm=FakeLLM([LLMReply(text="ok")]), deep_llm=None, home=tmp_path
    )
    agent.token_scale = 1.4  # learned from an earlier, better-behaved provider

    async for _ in agent.run_turn([], "hello"):
        pass

    assert agent.token_scale == 1.4
