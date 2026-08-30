"""Token estimation + tool-result eviction (pure, no LLM)."""

from pyrrhon.core.context import (
    FIT_CHEAP,
    FIT_FORCED,
    FIT_FULL,
    TOOL_STUB_MIN,
    compact_tool_results,
    estimate_tokens,
    fit_to_budget,
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


# -- the pre-flight ladder (M16b) -------------------------------------------


class CountingLLM:
    """Counts summarize round trips. The whole point of the ladder is that the
    expensive rung is rare, so the count is the assertion."""

    def __init__(self, text: str = "A summary."):
        self.text = text
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        return LLMReply(text=self.text)


def _bulky_history(results: int) -> list[dict]:
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "the original question"},
    ]
    for i in range(results):
        history.append({"role": "assistant", "content": f"looking at {i}"})
        history.append({"role": "tool", "content": f"{i}" * 4000})
    history.append({"role": "user", "content": "follow up"})
    return history


async def test_a_history_under_budget_is_left_alone():
    llm = CountingLLM()
    history = _bulky_history(2)
    before = [dict(m) for m in history]
    assert await fit_to_budget(history, llm, 1_000_000) == ""
    assert history == before
    assert llm.calls == 0


async def test_the_cheap_rung_is_enough_and_the_expensive_one_never_runs():
    """The ladder's reason for existing: three of its four rungs are pure,
    local and free, and the one that costs a round trip is genuinely last."""
    llm = CountingLLM()
    history = _bulky_history(4)
    # Comfortably above what the elided stubs will weigh, comfortably below
    # what the four full results weigh.
    assert await fit_to_budget(history, llm, 900) == "compact"
    assert llm.calls == 0
    assert history_tokens(history) <= 900


async def test_the_ladder_elides_harder_before_it_summarizes():
    """Rung 3 ignores the last-user boundary that rung 2 respects, which is the
    only thing hard_compact_tool_results ever did — a parameter now, not a
    second function."""
    llm = CountingLLM()
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "A" * 4000},
        {"role": "tool", "content": "B" * 4000},
    ]
    assert await fit_to_budget(history, llm, 400) == "hard"
    assert llm.calls == 0
    assert "elided" in history[3]["content"]
    assert history[4]["content"] == "B" * 4000  # the most recent survives


async def test_summarize_runs_only_once_eliding_has_run_out():
    llm = CountingLLM()
    history = [{"role": "system", "content": "sys"}]
    history += [{"role": "user", "content": "q" * 4000} for _ in range(12)]
    assert await fit_to_budget(history, llm, 500, keep_last=2) == "summarize"
    assert llm.calls == 1


async def test_a_zero_budget_means_never_compact():
    llm = CountingLLM()
    history = _bulky_history(4)
    before = [dict(m) for m in history]
    assert await fit_to_budget(history, llm, 0) == ""
    assert history == before


async def test_forcing_runs_every_rung_regardless_of_the_estimate():
    """The safety net's mode. The provider has already rejected the request, so
    the estimate is known wrong and nothing it says should gate a rung."""
    llm = CountingLLM()
    history = _bulky_history(2)
    assert await fit_to_budget(history, llm, 1_000_000, mode=FIT_FORCED, keep_last=2)
    assert "elided" in history[3]["content"]


async def test_the_ladder_can_be_asked_to_stop_before_the_round_trip():
    """What the turn's own pre-flight passes. M10 moved the summarize round
    trip off the critical path; the ladder must not quietly put it back."""
    llm = CountingLLM()
    history = [{"role": "system", "content": "sys"}]
    history += [{"role": "user", "content": "q" * 4000} for _ in range(12)]
    assert await fit_to_budget(history, llm, 500, keep_last=2, mode=FIT_CHEAP) == ""
    assert llm.calls == 0


async def test_the_cheap_mode_never_spends_the_current_turn_s_evidence():
    """The regression M16b's runtime pass found on a real account.

    With a learned ceiling of 8000 tokens the request budget for history is
    ~2012, which is less than the system prompt plus one tool result. The
    history is over budget from round one and never comes back under, so a
    ladder allowed to reach rung 3 reached it on EVERY round and stripped every
    tool result but the most recent — the current turn's evidence, which rung
    2's last-user boundary exists to protect. It achieved nothing (still over
    budget afterwards) and cost the model its working memory, so it re-read
    files it had already seen.

    Rung 3 is recovery-grade. The pre-flight runs what is free AND safe.
    """
    llm = CountingLLM()
    history = [
        {"role": "system", "content": "s" * 5000},
        {"role": "user", "content": "the question"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "first result " + "A" * 6000},
        {"role": "assistant", "content": "b"},
        {"role": "tool", "content": "second result " + "B" * 6000},
    ]
    rung = await fit_to_budget(history, llm, 2012, mode=FIT_CHEAP)

    assert history_tokens(history) > 2012, "the fixture must stay over budget"
    assert rung == ""  # nothing before the last user message to elide
    assert "elided" not in history[3]["content"]
    assert "elided" not in history[5]["content"]
    assert llm.calls == 0


async def test_the_full_mode_does_spend_it():
    """Dead time is where that cost belongs. Session's background pass runs
    this, which is the other half of restricting the pre-flight."""
    llm = CountingLLM()
    history = [
        {"role": "system", "content": "s" * 5000},
        {"role": "user", "content": "the question"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "first result " + "A" * 6000},
        {"role": "assistant", "content": "b"},
        {"role": "tool", "content": "second result " + "B" * 6000},
    ]
    rung = await fit_to_budget(history, llm, 2012, mode=FIT_FULL, keep_last=8)

    assert rung == "hard"
    intact = [m for m in history if m.get("role") == "tool" and "elided" not in m["content"]]
    assert len(intact) == 1
    assert "second result" in intact[0]["content"]  # the most recent survives
