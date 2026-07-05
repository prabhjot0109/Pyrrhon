# M8: Context Engineering & Deep Subagent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Pyrrhon's reasoning scale to large, fragmented, cyclic codebases without blowing the context window or the API bill: a code graph over the AST index, history compaction, tool-loop guards, a tool-equipped deep subagent, and provider-isolated STT/TTS with local-model support.

**Architecture:** Three independent hardening passes over the existing headless core. (A) Context engineering: token budgeting + tool-result eviction + LLM summarization live inside `Agent.run_turn`, so every channel gets them for free. (B) Retrieval stays **agentic and vectorless** — the tree-sitter SQLite index grows an import graph and a ranked repo map (no embeddings, no vector DB). (C) `think_deeper` becomes a bounded subagent loop with its own read-only tool belt and fresh context, cancellable through the existing `Session` task machinery. Voice gains an STT/TTS provider registry mirroring the LLM slot pattern.

**Tech Stack:** Python ≥3.12, uv, tree-sitter + tree-sitter-language-pack (already installed), SQLite (stdlib), pydantic settings, Pipecat 1.5.0 voice services. **No new runtime dependencies for phases A–C.**

## Decision Record (answers to the architectural questions)

These decisions are binding for this plan. Each one names the alternative that was rejected and why.

1. **No vector database / embeddings RAG.** Rejected: vector RAG adds an embedding-model dependency, index-staleness on every edit, retrieval latency in the voice path, and *approximate* results that can't be cited as `path:line` (violates the grounding hard rule). The industry converged the same way: Cline ("Why Cline Doesn't Index Your Codebase", 2025) uses tree-sitter AST + ripgrep; Cursor/Amp followed; Aider uses a tree-sitter repo map ranked by reference counts; Claude Code and Codex CLI are grep-first. Pyrrhon already has the right skeleton (grep + AST symbol index + git); this plan deepens it instead of replacing it. GraphRAG is also rejected — the SQLite `symbols`/`refs`/`imports` tables *are* the code graph; we query it with tools instead of precomputing embeddings over it.
2. **No new memory system.** `RememberTool` + soul files + (new) compaction summaries cover v1. Supermemory/self-evolving memory is scope creep per `VISION.md` scope discipline — parked until the Understand loop is undeniable.
3. **AST approach is right, but too shallow today.** `find_symbol`/`find_references` are name-global (every `run` matches every `run`) and there is no dependency edge at all. Fix: an `imports` table (module-level edges, relative imports resolved at index time) and a ranked **repo map** (Aider-style: top symbols per file scored by cross-file reference counts, token-budgeted). Ranking is pure counting — no graph traversal — so **cyclic dependencies cannot cause recursion**; cycles just show up as edges in both directions.
4. **Context-window management did not exist; now it lives in the agent loop.** Two mechanisms, both provider-agnostic (chars/4 token estimate, no tiktoken dependency): (a) *tool-result eviction* — bulky tool outputs from earlier turns are elided to stubs after use (the model can re-run the tool; the grounding gate verifies citations against the repo, not against history, so eviction never breaks grounding); (b) *auto-summarization* — when estimated history tokens exceed `[context] budget_tokens`, older turns are summarized by the fast model into one system message that must preserve `path:line` citations verbatim; system messages (mode prompts) are never summarized away. This is the same shape as Cline's Auto Compact and Claude Code's context editing.
5. **Loop / cost / hallucination guards.** Per turn: duplicate tool-call detection (same name + args → skipped with a nudge, no execution), per-result char cap (8k), cumulative tool-output cap (40k chars), existing `max_tool_rounds=8`. On any budget exhaustion the model gets exactly **one** tools-off call to answer from gathered evidence (instead of today's canned apology). Escalation depth is structurally 1: the deep subagent's tool belt does not include `think_deeper`, so recursive self-escalation is impossible. The grounding gate remains the hallucination backstop on final text.
6. **The `thinkdeeper_subagent_harness.png` diagram is directionally right, with three corrections.** ✔ Right: isolated context, deep model behind a tool, subagent runs repo/AST/git tools itself, compiles a compact report back to the fast model. ✘ Corrections: (a) *not fire-and-forget async* — the subagent runs inside the turn as an awaited tool call, which the existing `Session.abort_current_turn()` already makes cancellable on barge-in; a silent background worker would break voice turn-taking. Instead the fast model **narrates before dispatching** ("give me a second, I'm tracing that…") — the loop now speaks assistant text that accompanies tool calls, so the user hears progress. (b) The diagram omits budgets — the subagent gets its own `ToolGuard` + round cap + a word-capped report format. (c) The report is not trusted blindly: it flows back as a tool result and the fast model's final answer still passes the grounding gate, so any `path:line` the deep model fabricates is stripped before speech.
7. **STT/TTS/LLM provider isolation.** LLM was already isolated (OpenAI-compat adapter + fallback chains). STT/TTS were hard-coded to Groq/OpenAI classes in `pipeline.py`; this plan adds `pyrrhon/voice/providers.py` factories keyed by `[voice] stt_provider` / `tts_provider` config, with lazy imports and graceful `VoiceUnavailableError` degradation.
8. **OpenAI TTS is not the optimal real-time choice — but stays the default.** Benchmarks (Coval 2026, CodeSOTA 2026): OpenAI TTS ≈380ms–2.3s time-to-first-audio with huge variance; Cartesia Sonic ≈90–190ms; ElevenLabs Flash ≈75–290ms; Deepgram Aura-2 ≈120–313ms. Registry ships `openai` (default — no new key for existing users), `cartesia` (recommended, documented), `elevenlabs`, and `piper` (local, free, ~35ms on CPU via a local server). AssemblyAI is an STT vendor, not TTS. Local STT: Pipecat's Whisper service (faster-whisper, no key). Local LLM: `ollama`/`lmstudio` builtin providers via the existing OpenAI-compat adapter (they only need a base_url and a keyless auth path).
9. **Harness inspiration adopted:** Cline (vectorless AST+grep exploration, Auto Compact, focus-on-plan), Aider (ranked repo map), Claude Code (tool-result eviction, subagents with isolated context, narrate-then-dispatch). Multi-agent orchestration beyond one deep subagent is rejected (Cline's "seductive trap" #1) — one fast voice loop + one deep worker is the whole topology.

## Global Constraints

- Python >= 3.12 (`.python-version`); manage everything with `uv` (`uv sync`, `uv run pytest`).
- No new runtime dependencies in phases A–C (stdlib + already-installed tree-sitter only). Phase D adds only optional pipecat extras.
- Grounding hard rule: every claim cites real `path:line` or says "I'm not certain" — nothing in this plan may bypass the `GroundingGate` on spoken text.
- Real-time discipline: no filesystem or CPU-bound work on the event loop — offload via `asyncio.to_thread` (pattern in `pyrrhon/core/tools/repo.py:1-6`).
- Turns must stay cancellable: any new awaited work inside `Agent.run_turn` or tools is automatically covered by `Session.abort_current_turn()`; never spawn detached tasks that outlive the turn.
- History mutation rules: history records what the user was shown; drafts, retry prompts, and forced-answer nudges never enter `history`.
- Backward compatibility: all new `Agent`/`ThinkDeeperTool` constructor params are keyword args with defaults; existing tests must keep passing.
- Windows-safe: no POSIX-only APIs; paths via `pathlib`, posix-style repo-relative strings.
- Commit style: `feat(scope): …` / `test(scope): …`, matching git history.
- Async tests: bare `async def test_…` (pytest asyncio auto mode, matching `tests/test_escalation.py`).

## File Structure

```
pyrrhon/core/context.py            NEW  token estimate, tool-result eviction, history summarization
pyrrhon/core/agent/guards.py       NEW  ToolGuard (dup/size budgets), assistant_tool_message (moved)
pyrrhon/core/agent/loop.py         MOD  compaction call, guards, _emit_final refactor, narration
pyrrhon/core/agent/escalate.py     MOD  ThinkDeeperTool grows a bounded tool loop
pyrrhon/core/agent/prompts.py      MOD  DEEP_AGENT_PROMPT, updated ESCALATION_NOTE
pyrrhon/core/tools/ast_index.py    MOD  imports table, DependenciesTool, repo map, RepoMapTool
pyrrhon/config/settings.py         MOD  ContextSettings, VoiceSettings providers, keyless providers
pyrrhon/core/providers/llm.py      MOD  keyless (local) provider support
pyrrhon/voice/providers.py         NEW  STT/TTS registry + VoiceUnavailableError (moved)
pyrrhon/voice/pipeline.py          MOD  use the registry
pyrrhon/repl.py                    MOD  build_agent wires deep_tools, new tools, context budget
tests/test_context.py              NEW
tests/test_loop_guards.py          NEW
tests/test_import_graph.py         NEW
tests/test_repo_map.py             NEW
tests/test_escalation.py           MOD  subagent-loop tests
tests/test_voice_providers.py      NEW
tests/test_settings.py             MOD  new sections
```

---

## Phase A — Context engineering

### Task 1: Token estimation and tool-result eviction (`pyrrhon/core/context.py`)

**Files:**
- Create: `pyrrhon/core/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces: `estimate_tokens(text: str) -> int`, `history_tokens(history: list[dict]) -> int`, `compact_tool_results(history: list[dict]) -> int` (returns number of messages elided; mutates history in place). Constants `TOOL_STUB_KEEP = 300`, `TOOL_STUB_MIN = 600`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_context.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.core.context'`

- [ ] **Step 3: Write the implementation**

Create `pyrrhon/core/context.py`:

```python
"""Context engineering: token budgeting, tool-result eviction, summarization.

Provider-agnostic by design: tokens are estimated as len(text)//4 — good
enough for budgeting, and it avoids a tokenizer dependency that would have
to match whichever provider the user configured.

Eviction never breaks grounding: the GroundingGate verifies citations
against the repo itself (pyrrhon/core/grounding/), not against history, so
eliding a stale grep dump costs nothing but a possible tool re-run.
"""

from __future__ import annotations

import json

TOOL_STUB_KEEP = 300  # chars of a tool result kept when elided
TOOL_STUB_MIN = 600   # results at or below this size are never elided


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def history_tokens(history: list[dict]) -> int:
    total = 0
    for message in history:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        for call in message.get("tool_calls") or ():
            total += estimate_tokens(json.dumps(call, default=str))
    return total


def compact_tool_results(history: list[dict]) -> int:
    """Elide bulky tool results from turns BEFORE the latest user message.

    The current turn's results stay intact — the model may still be reading
    them. Idempotent: an elided stub is shorter than TOOL_STUB_MIN, so it
    never matches the size check again. Mutates history in place; returns
    the number of messages elided.
    """
    last_user = None
    for i, message in enumerate(history):
        if message.get("role") == "user":
            last_user = i
    if last_user is None:
        return 0
    elided = 0
    for message in history[:last_user]:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= TOOL_STUB_MIN:
            continue
        dropped = len(content) - TOOL_STUB_KEEP
        message["content"] = (
            content[:TOOL_STUB_KEEP]
            + f"\n…[{dropped} chars elided after use — re-run the tool if needed]"
        )
        elided += 1
    return elided
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/context.py tests/test_context.py
git commit -m "feat(context): token estimation and tool-result eviction"
```

### Task 2: History summarization + wiring compaction into the agent loop

**Files:**
- Modify: `pyrrhon/core/context.py` (add `maybe_summarize`)
- Modify: `pyrrhon/config/settings.py` (add `ContextSettings`)
- Modify: `pyrrhon/core/agent/loop.py:93-99` (call compaction at turn start; new `context_budget_tokens` param)
- Modify: `pyrrhon/repl.py` (`build_agent` passes the budget)
- Test: `tests/test_context.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: `history_tokens`, `estimate_tokens` from Task 1; `FakeLLM` from `tests/helpers.py`; any object with `async chat(messages, tools=None) -> LLMReply`.
- Produces: `async maybe_summarize(history: list[dict], llm, budget_tokens: int, keep_last: int = 8) -> bool` (True if it compacted); `Settings.context: ContextSettings` with `budget_tokens: int = 32000`, `keep_last_messages: int = 8`; `Agent(..., context_budget_tokens: int = 32000, context_keep_last: int = 8)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context.py`:

```python
from pyrrhon.core.context import maybe_summarize
from pyrrhon.core.providers.llm import LLMReply
from tests.helpers import FakeLLM


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
```

Append to `tests/test_settings.py`:

```python
def test_context_settings_defaults_and_override(tmp_path):
    from pyrrhon.config.settings import load_settings

    assert load_settings(tmp_path).context.budget_tokens == 32000
    (tmp_path / ".pyrrhon.toml").write_text(
        "[context]\nbudget_tokens = 9000\nkeep_last_messages = 4\n", encoding="utf-8"
    )
    settings = load_settings(tmp_path)
    assert settings.context.budget_tokens == 9000
    assert settings.context.keep_last_messages == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context.py tests/test_settings.py -v`
Expected: FAIL — `ImportError: cannot import name 'maybe_summarize'` and `AttributeError: 'Settings' object has no attribute 'context'`

- [ ] **Step 3: Implement `maybe_summarize`**

Append to `pyrrhon/core/context.py`:

```python
SUMMARY_PROMPT = (
    "Summarize the conversation below so it can be continued later. Keep: the "
    "user's goals, decisions made, key findings about the codebase, and EVERY "
    "path:line citation EXACTLY as written (e.g. utils/helpers.py:12). Drop "
    "tool output noise and dead ends. 300 words maximum. Output only the summary."
)

SUMMARY_HEADER = "Summary of earlier conversation (older turns were compacted):\n"


def _render(messages: list[dict]) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "?")
        content = message.get("content")
        if isinstance(content, str) and content:
            lines.append(f"{role}: {content}")
        for call in message.get("tool_calls") or ():
            name = call.get("function", {}).get("name", "?")
            args = call.get("function", {}).get("arguments", "")
            lines.append(f"{role} called tool {name}({args})")
    return "\n".join(lines)


async def maybe_summarize(
    history: list[dict], llm, budget_tokens: int, keep_last: int = 8
) -> bool:
    """Compress old turns into one system summary when over budget.

    history[0] (the base system prompt) and the last `keep_last` messages
    survive verbatim; system messages in the compacted span (mode prompts)
    are kept in place, not summarized away. The split never strands a tool
    result without its parent assistant tool_calls message. Any LLM failure
    leaves history untouched — compaction is an optimization, never a
    correctness requirement.
    """
    if history_tokens(history) <= budget_tokens:
        return False
    split = max(len(history) - keep_last, 1)
    while split > 1 and history[split].get("role") == "tool":
        split -= 1
    if split <= 1:
        return False
    middle = history[1:split]
    try:
        reply = await llm.chat(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": _render(middle)},
            ],
            tools=None,
        )
    except Exception:
        return False
    if not reply.text:
        return False
    kept_system = [m for m in middle if m.get("role") == "system"]
    history[1:split] = [
        *kept_system,
        {"role": "system", "content": SUMMARY_HEADER + reply.text},
    ]
    return True
```

- [ ] **Step 4: Add `ContextSettings` to `pyrrhon/config/settings.py`**

Insert after the `VoiceSettings` class:

```python
class ContextSettings(BaseModel):
    """Context-window budgeting (TOML section [context])."""

    budget_tokens: int = 32000       # estimated-token ceiling before compaction
    keep_last_messages: int = 8      # recent messages kept verbatim
```

and add one field to `Settings` (next to `voice`):

```python
    context: ContextSettings = ContextSettings()
```

- [ ] **Step 5: Wire compaction into `Agent.run_turn`**

In `pyrrhon/core/agent/loop.py`, add imports:

```python
from pyrrhon.core.context import compact_tool_results, maybe_summarize
```

Add the constructor params (after `mode: str = "understand"`):

```python
        context_budget_tokens: int = 32000,
        context_keep_last: int = 8,
```

with assignments in `__init__` body:

```python
        self.context_budget_tokens = context_budget_tokens
        self.context_keep_last = context_keep_last
```

Change the top of `run_turn` (currently `loop.py:96-99`) to:

```python
        if not history:
            history.append({"role": "system", "content": self.system_prompt})
        history.append({"role": "user", "content": user_text})
        compact_tool_results(history)
        if self.context_budget_tokens:
            await maybe_summarize(
                history,
                self.llm,
                self.context_budget_tokens,
                keep_last=self.context_keep_last,
            )
        schemas = [tool.schema() for tool in self.tools.values()]
```

In `pyrrhon/repl.py` `build_agent`, pass both in the `Agent(...)` call:

```python
        context_budget_tokens=settings.context.budget_tokens,
        context_keep_last=settings.context.keep_last_messages,
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS (new + existing; the compaction is a no-op for short test histories).

- [ ] **Step 7: Commit**

```bash
git add pyrrhon/core/context.py pyrrhon/config/settings.py pyrrhon/core/agent/loop.py pyrrhon/repl.py tests/test_context.py tests/test_settings.py
git commit -m "feat(context): auto-summarization budget wired into the agent loop"
```

### Task 3: Tool-loop guards + forced final answer

**Files:**
- Create: `pyrrhon/core/agent/guards.py`
- Modify: `pyrrhon/core/agent/loop.py` (guards in `run_turn`, `_emit_final` refactor, `_forced_answer`; move `_assistant_tool_message`)
- Test: `tests/test_loop_guards.py`

**Interfaces:**
- Consumes: `Agent`, `FakeLLM`, `LLMReply`, `ToolCall`.
- Produces: `ToolGuard` with `is_duplicate(name: str, args: dict) -> bool`, `clip(result: str) -> str`, property `exhausted: bool`; module constants `MAX_TOOL_RESULT_CHARS = 8000`, `MAX_TURN_TOOL_CHARS = 40000`, `DUPLICATE_NOTE`; `assistant_tool_message(reply: LLMReply) -> dict` (moved from loop.py — Task 7 imports it from guards to avoid a circular import). `Agent` grows private helpers `_forced_answer(history) -> str` and `_emit_final(history, text)` (async generator of events).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_loop_guards.py`:

```python
"""Duplicate-call, size-cap, and budget-exhaustion guards on the tool loop."""

from pathlib import Path

from pyrrhon.core.agent.guards import (
    DUPLICATE_NOTE,
    MAX_TOOL_RESULT_CHARS,
    ToolGuard,
)
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.base import Tool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class EchoTool(Tool):
    name = "echo"
    description = "echoes"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"]}

    def __init__(self):
        self.calls = 0

    async def run(self, text: str) -> str:
        self.calls += 1
        return f"echo: {text}"


def test_guard_flags_exact_duplicates_only():
    guard = ToolGuard()
    assert guard.is_duplicate("grep", {"pattern": "a"}) is False
    assert guard.is_duplicate("grep", {"pattern": "a"}) is True
    assert guard.is_duplicate("grep", {"pattern": "b"}) is False


def test_guard_clips_and_tracks_budget():
    guard = ToolGuard(max_result_chars=10, max_total_chars=15)
    clipped = guard.clip("x" * 50)
    assert clipped.startswith("xxxxxxxxxx")
    assert "truncated" in clipped
    assert guard.exhausted  # 10 + suffix >= 15


def _call(name: str, args: dict, call_id: str = "c1") -> LLMReply:
    return LLMReply(tool_calls=(ToolCall(id=call_id, name=name, arguments=args),))


async def test_duplicate_tool_call_is_skipped_not_rerun():
    tool = EchoTool()
    fast = FakeLLM([
        _call("echo", {"text": "hi"}, "c1"),
        _call("echo", {"text": "hi"}, "c2"),   # exact duplicate
        LLMReply(text="done"),
    ])
    agent = Agent(llm=fast, tools=[tool], system_prompt="p", repo_root=FIXTURE)
    events = [e async for e in agent.run_turn([], "q")]
    assert tool.calls == 1                     # second call never executed
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert DUPLICATE_NOTE.format(name="echo") in finished[1].result_preview \
        or "already called" in finished[1].result_preview


async def test_round_exhaustion_gets_one_forced_answer():
    replies = [_call("echo", {"text": str(i)}, f"c{i}") for i in range(3)]
    replies.append(LLMReply(text="Best-effort answer from evidence."))  # tools=None call
    fast = FakeLLM(replies)
    agent = Agent(
        llm=fast, tools=[EchoTool()], system_prompt="p",
        repo_root=FIXTURE, max_tool_rounds=3,
    )
    history: list[dict] = []
    events = [e async for e in agent.run_turn(history, "q")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "Best-effort answer from evidence."
    # The forced call must disable tools…
    assert fast.calls[-1]["tools"] is None
    # …and its nudge prompt must NOT be persisted.
    assert all("exhausted" not in str(m.get("content")) for m in history)


async def test_forced_answer_falls_back_to_canned_text_on_failure():
    class FlakyLLM:
        def __init__(self, replies):
            self._replies = replies

        async def chat(self, messages, tools=None):
            if tools is None:
                raise RuntimeError("boom")
            return self._replies.pop(0)

    flaky = FlakyLLM([_call("echo", {"text": "x"})])
    agent = Agent(
        llm=flaky, tools=[EchoTool()], system_prompt="p",
        repo_root=FIXTURE, max_tool_rounds=1,
    )
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert "budget" in speech[-1].text  # canned fallback
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_loop_guards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.core.agent.guards'`

- [ ] **Step 3: Create `pyrrhon/core/agent/guards.py`**

```python
"""Per-turn tool-usage guards, shared by the fast loop and the deep subagent.

These are the runaway-cost brakes: an LLM that re-issues the same call, or
dumps megabytes of grep output into context, burns tokens without progress.
The guard never blocks NEW work — only exact repeats and oversized output.
"""

from __future__ import annotations

import json

MAX_TOOL_RESULT_CHARS = 8_000    # per tool result
MAX_TURN_TOOL_CHARS = 40_000     # cumulative per turn / per subagent run

DUPLICATE_NOTE = (
    "NOTE: you already called {name} with exactly these arguments this turn; "
    "the result has not changed. Use what you have or try different arguments."
)


class ToolGuard:
    def __init__(
        self,
        max_result_chars: int = MAX_TOOL_RESULT_CHARS,
        max_total_chars: int = MAX_TURN_TOOL_CHARS,
    ):
        self.max_result_chars = max_result_chars
        self.max_total_chars = max_total_chars
        self._seen: set[tuple[str, str]] = set()
        self._spent = 0

    def is_duplicate(self, name: str, args: dict) -> bool:
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def clip(self, result: str) -> str:
        if len(result) > self.max_result_chars:
            result = (
                result[: self.max_result_chars]
                + "\n…[truncated — result exceeded the per-call cap]"
            )
        self._spent += len(result)
        return result

    @property
    def exhausted(self) -> bool:
        return self._spent >= self.max_total_chars


def assistant_tool_message(reply) -> dict:
    """Chat-API-shaped assistant message carrying tool calls.

    Lives here (not loop.py) so escalate.py can use it without a circular
    import — loop.py imports ThinkDeeperTool from escalate.py.
    """
    return {
        "role": "assistant",
        "content": reply.text,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in reply.tool_calls
        ],
    }
```

- [ ] **Step 4: Rework `Agent.run_turn` in `pyrrhon/core/agent/loop.py`**

Add imports:

```python
from pyrrhon.core.agent.guards import DUPLICATE_NOTE, ToolGuard, assistant_tool_message
```

Add a module constant next to `PREVIEW_LEN`:

```python
BUDGET_MESSAGE = (
    "I hit my tool budget for this question — ask me to continue "
    "and I'll keep digging."
)
```

Replace `run_turn`, delete the old module-level `_assistant_tool_message` (now `guards.assistant_tool_message`), and keep `_run_tool` unchanged:

```python
    async def run_turn(
        self, history: list[dict], user_text: str
    ) -> AsyncIterator[Event]:
        if not history:
            history.append({"role": "system", "content": self.system_prompt})
        history.append({"role": "user", "content": user_text})
        compact_tool_results(history)
        if self.context_budget_tokens:
            await maybe_summarize(
                history,
                self.llm,
                self.context_budget_tokens,
                keep_last=self.context_keep_last,
            )
        schemas = [tool.schema() for tool in self.tools.values()]
        guard = ToolGuard()

        for _ in range(self.max_tool_rounds):
            reply = await self.llm.chat(history, tools=schemas)
            if not reply.tool_calls:
                async for event in self._emit_final(history, reply.text or "(no answer)"):
                    yield event
                return

            history.append(assistant_tool_message(reply))
            for call in reply.tool_calls:
                yield ToolCallStarted(name=call.name, args=call.arguments)
                if guard.is_duplicate(call.name, call.arguments):
                    result = DUPLICATE_NOTE.format(name=call.name)
                else:
                    result = guard.clip(await self._run_tool(call.name, call.arguments))
                history.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
                yield ToolCallFinished(name=call.name, result_preview=result[:PREVIEW_LEN])
            if guard.exhausted:
                break

        # Budget exhausted (rounds or output volume): ONE answer-only call so
        # the evidence gathered so far isn't wasted on a canned apology.
        text = await self._forced_answer(history)
        async for event in self._emit_final(history, text):
            yield event

    async def _forced_answer(self, history: list[dict]) -> str:
        nudge = {
            "role": "user",
            "content": (
                "Tool budget for this question is exhausted. Answer now from "
                "the evidence above, citing only path:line locations you "
                "actually saw. If the evidence is insufficient, say what you "
                "would look at next."
            ),
        }
        try:
            # The nudge never enters `history` — same rule as retry prompts.
            reply = await self.llm.chat([*history, nudge], tools=None)
        except Exception:
            return BUDGET_MESSAGE
        return reply.text or BUDGET_MESSAGE

    async def _emit_final(
        self, history: list[dict], text: str
    ) -> AsyncIterator[Event]:
        """Gate, record, and emit the turn's final text (was inline in run_turn)."""
        if self.grounding_gate is None:
            history.append({"role": "assistant", "content": text})
            yield SpeechChunk(text=text)
            for citation in await asyncio.to_thread(
                extract_citations, text, self.repo_root
            ):
                yield citation
            if self.mode == "design":
                question = extract_question(text)
                if question is not None:
                    yield AskUser(question=question)
            return

        gated = await self.grounding_gate.check(text)
        if gated.unverified and self.allow_retry:
            retry_messages = [
                *history,
                {"role": "assistant", "content": text},
                {"role": "user", "content": _retry_prompt(gated.unverified)},
            ]
            retry_reply = await self.llm.chat(retry_messages, tools=None)
            text = retry_reply.text or text
            gated = await self.grounding_gate.check(text)

        history.append({"role": "assistant", "content": gated.speech_text})
        yield SpeechChunk(text=gated.speech_text)
        for citation in gated.citations:
            yield citation
        if self.mode == "design":
            question = extract_question(gated.speech_text)
            if question is not None:
                yield AskUser(question=question)
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS. Note: any existing test asserting the exact old budget text still passes (`BUDGET_MESSAGE` is char-identical); tests referencing `loop._assistant_tool_message` (if any) must be updated to `guards.assistant_tool_message`.

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent/guards.py pyrrhon/core/agent/loop.py tests/test_loop_guards.py
git commit -m "feat(agent): duplicate/size tool guards and forced final answer"
```

---

## Phase B — Code graph over the AST index

### Task 4: Import graph + `list_dependencies` tool

**Files:**
- Modify: `pyrrhon/core/tools/ast_index.py` (imports table, extraction, `DependenciesTool`)
- Modify: `pyrrhon/repl.py` (register the tool for the fast model)
- Test: `tests/test_import_graph.py`

**Interfaces:**
- Consumes: existing `SymbolIndex` internals (`_reparse`, `_forget`, `_SCHEMA`, `ensure_fresh`).
- Produces: `SymbolIndex.list_imports(rel_file: str) -> list[str]` (module names, awaitable), `SymbolIndex.find_importers(rel_file: str) -> list[str]` (repo-relative files, awaitable), pure helpers `_module_name(rel: str) -> str`, `_package_of(rel: str) -> str`, `_modules_from_import(stmt_text: str, package: str) -> list[str]`; `DependenciesTool(index)` with tool name `list_dependencies` taking `path`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_import_graph.py`:

```python
"""Module-level import graph in the SymbolIndex."""

from pyrrhon.core.tools.ast_index import (
    DependenciesTool,
    SymbolIndex,
    _module_name,
    _modules_from_import,
    _package_of,
)


def test_module_and_package_names():
    assert _module_name("pkg/sub/mod.py") == "pkg.sub.mod"
    assert _module_name("pkg/sub/__init__.py") == "pkg.sub"
    assert _package_of("pkg/sub/mod.py") == "pkg.sub"
    assert _package_of("pkg/sub/__init__.py") == "pkg.sub"
    assert _package_of("app.py") == ""


def test_modules_from_import_statements():
    assert _modules_from_import("import os", "") == ["os"]
    assert _modules_from_import("import a.b, c as d", "") == ["a.b", "c"]
    # from-imports record the module AND each name as a candidate submodule,
    # so `from pkg import mod` still creates an edge to pkg.mod.
    assert _modules_from_import("from pkg.core import session, events", "") == [
        "pkg.core", "pkg.core.session", "pkg.core.events",
    ]
    assert _modules_from_import("from .utils import helper", "pkg.sub") == [
        "pkg.sub.utils", "pkg.sub.utils.helper",
    ]
    assert _modules_from_import("from ..core import gate", "pkg.sub") == [
        "pkg.core", "pkg.core.gate",
    ]
    assert _modules_from_import("from x import *", "") == ["x"]


def _make_repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "service.py").write_text(
        "def handle():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "api.py").write_text(
        "from pkg.service import handle\n\ndef route():\n    return handle()\n",
        encoding="utf-8",
    )
    (tmp_path / "cli.py").write_text(
        "from pkg import api\n\ndef main():\n    return api.route()\n",
        encoding="utf-8",
    )
    return tmp_path


async def test_list_imports_and_find_importers(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    assert "pkg.service" in await index.list_imports("pkg/api.py")
    importers = await index.find_importers("pkg/service.py")
    assert importers == ["pkg/api.py"]
    # `from pkg import api` in cli.py creates the candidate edge pkg.api:
    assert await index.find_importers("pkg/api.py") == ["cli.py"]


async def test_dependencies_tool_formats_both_directions(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    out = await DependenciesTool(index).run(path="pkg/api.py")
    assert "imports:" in out
    assert "pkg.service" in out
    assert "imported by:" in out
    assert "cli.py" in out


async def test_cyclic_imports_do_not_break_queries(tmp_path):
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    assert await index.find_importers("a.py") == ["b.py"]
    assert await index.find_importers("b.py") == ["a.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_import_graph.py -v`
Expected: FAIL — `ImportError: cannot import name 'DependenciesTool'`

- [ ] **Step 3: Implement in `pyrrhon/core/tools/ast_index.py`**

Add to the schema string (`_SCHEMA`):

```python
CREATE TABLE IF NOT EXISTS imports (file TEXT, module TEXT);
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports (module);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports (file);
```

Add the import-statement query next to `_REF_QUERY` (whole statements — their text is parsed in Python, which is robust across grammar details):

```python
_IMPORT_QUERY = Query(
    _PY_LANGUAGE,
    """
    (import_statement) @import
    (import_from_statement) @import
    """,
)
```

Add pure helpers at module level:

```python
def _module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_of(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts:
        parts.pop()  # __init__ or the module file — either way, drop it
    return ".".join(parts)


def _modules_from_import(stmt_text: str, package: str) -> list[str]:
    """Module names referenced by one import statement.

    from-imports also record `module.name` for each imported name: the name
    may be a submodule (`from pkg import api`) or an attribute — a false
    attribute edge is harmless, a missed submodule edge is not.
    """
    text = " ".join(stmt_text.split())
    if text.startswith("from "):
        module_part, _, names_part = text[len("from "):].partition(" import ")
        module = _resolve_relative(module_part.strip(), package)
        if not module:
            return []
        if names_part.strip() == "*":
            return [module]
        modules = [module]
        for name in names_part.replace("(", "").replace(")", "").split(","):
            name = name.strip().split(" as ")[0].strip()
            if name:
                modules.append(f"{module}.{name}")
        return modules
    modules = []
    for part in text[len("import "):].split(","):
        module = part.strip().split(" as ")[0].strip()
        if module:
            modules.append(module)
    return modules


def _resolve_relative(module: str, package: str) -> str:
    if not module.startswith("."):
        return module
    dots = len(module) - len(module.lstrip("."))
    remainder = module.lstrip(".")
    parts = package.split(".") if package else []
    if dots - 1:
        parts = parts[: -(dots - 1)] if len(parts) >= dots - 1 else []
    base = ".".join(parts)
    if remainder and base:
        return f"{base}.{remainder}"
    return remainder or base
```

In `_reparse`, delete + re-extract imports (add after the refs loop, before the files upsert):

```python
        conn.execute("DELETE FROM imports WHERE file = ?", (rel,))
        package = _package_of(rel)
        for nodes in QueryCursor(_IMPORT_QUERY).captures(tree.root_node).values():
            for node in nodes:
                stmt = node.text.decode("utf-8")
                for module in _modules_from_import(stmt, package):
                    conn.execute(
                        "INSERT INTO imports (file, module) VALUES (?, ?)",
                        (rel, module),
                    )
```

In `_forget`, add:

```python
        conn.execute("DELETE FROM imports WHERE file = ?", (rel,))
```

Add query methods on `SymbolIndex` (async wrappers + sync bodies, same pattern as `find_symbol`):

```python
    async def list_imports(self, rel_file: str) -> list[str]:
        return await asyncio.to_thread(self._sync_list_imports, rel_file)

    async def find_importers(self, rel_file: str) -> list[str]:
        return await asyncio.to_thread(self._sync_find_importers, rel_file)

    def _sync_list_imports(self, rel_file: str) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT module FROM imports WHERE file = ? ORDER BY module",
                (rel_file,),
            ).fetchall()
        finally:
            conn.close()
        return [module for (module,) in rows]

    def _sync_find_importers(self, rel_file: str) -> list[str]:
        module = _module_name(rel_file)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT file FROM imports WHERE module = ? "
                "AND file != ? ORDER BY file",
                (module, rel_file),
            ).fetchall()
        finally:
            conn.close()
        return [file for (file,) in rows]
```

Add the tool class at the bottom of the file:

```python
class DependenciesTool(Tool):
    name = "list_dependencies"
    description = (
        "Show a Python file's import edges both ways: modules it imports, and "
        "repo files that import it. Answers 'what depends on this?' / "
        "'what does this rely on?' before you trace call sites."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative .py path"},
        },
        "required": ["path"],
    }

    def __init__(self, index: SymbolIndex):
        self.index = index

    async def run(self, path: str) -> str:
        await self.index.ensure_fresh()
        imports = await self.index.list_imports(path)
        importers = await self.index.find_importers(path)
        if not imports and not importers:
            return f"No import edges recorded for {path} (is it a Python file in the repo?)."
        lines = ["imports:"]
        lines += [f"  {m}" for m in imports] or ["  (none)"]
        lines.append("imported by:")
        lines += [f"  {f}" for f in importers] or ["  (none)"]
        return "\n".join(lines)
```

- [ ] **Step 4: Register for the fast model**

In `pyrrhon/repl.py` `build_agent`, add to the `tools` list after `FindReferencesTool(index)`:

```python
        DependenciesTool(index),
```

(and add `DependenciesTool` to the existing `ast_index` import line).

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_import_graph.py tests/test_symbol_index.py tests/test_ast_tools.py -v`
Expected: all PASS (existing DBs pick the new table up because `_SCHEMA` runs on every connect with `IF NOT EXISTS`; files indexed before this change get imports on their next mtime change — acceptable, note it in the commit body).

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/tools/ast_index.py pyrrhon/repl.py tests/test_import_graph.py
git commit -m "feat(ast): module import graph and list_dependencies tool"
```

### Task 5: Ranked repo map

**Files:**
- Modify: `pyrrhon/core/tools/ast_index.py` (`build_repo_map`, `RepoMapTool`)
- Modify: `pyrrhon/repl.py` (register)
- Test: `tests/test_repo_map.py`

**Interfaces:**
- Consumes: `symbols` + `refs` tables from the existing index.
- Produces: `SymbolIndex.build_repo_map(max_chars: int = 6000) -> str` (awaitable); `RepoMapTool(index)` named `repo_map`, no required params. Ranking = cross-file reference counts (pure counting: **no traversal, cycle-proof**).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_repo_map.py`:

```python
"""Ranked repo map: most-referenced symbols per file, token-budgeted."""

from pyrrhon.core.tools.ast_index import RepoMapTool, SymbolIndex


def _make_repo(tmp_path):
    (tmp_path / "core.py").write_text(
        "def hot():\n    return 1\n\ndef cold():\n    return 2\n", encoding="utf-8"
    )
    for i in range(3):  # three files call hot(); nothing calls cold()
        (tmp_path / f"user{i}.py").write_text(
            "from core import hot\n\ndef go():\n    return hot()\n", encoding="utf-8"
        )
    return tmp_path


async def test_repo_map_ranks_hot_symbols_first(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    repo_map = await index.build_repo_map()
    assert "core.py" in repo_map
    assert repo_map.index("hot") < repo_map.index("cold")   # within core.py
    assert repo_map.splitlines()[0].startswith("core.py")   # hottest file first


async def test_repo_map_respects_char_budget(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    small = await index.build_repo_map(max_chars=80)
    assert len(small) <= 80 + 40  # budget plus one truncation notice line


async def test_repo_map_tool_runs_end_to_end(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    out = await RepoMapTool(index).run()
    assert "core.py" in out
    assert ":" in out  # symbols carry line numbers for citation
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repo_map.py -v`
Expected: FAIL — `ImportError: cannot import name 'RepoMapTool'`

- [ ] **Step 3: Implement**

Add to `SymbolIndex`:

```python
    async def build_repo_map(self, max_chars: int = 6000) -> str:
        return await asyncio.to_thread(self._sync_build_repo_map, max_chars)

    def _sync_build_repo_map(self, max_chars: int) -> str:
        """Aider-style repo map: files ordered by how much the rest of the
        repo references their symbols; top symbols listed per file. Pure
        counting over the refs table — no graph traversal, so import cycles
        cannot recurse."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT s.file, s.name, s.kind, s.line,
                       (SELECT COUNT(*) FROM refs r
                         WHERE r.name = s.name AND r.file != s.file) AS uses
                FROM symbols s
                ORDER BY s.file, uses DESC, s.line
                """
            ).fetchall()
        finally:
            conn.close()
        by_file: dict[str, list[tuple[str, str, int, int]]] = {}
        for file, name, kind, line, uses in rows:
            by_file.setdefault(file, []).append((name, kind, line, uses))
        ranked = sorted(
            by_file.items(),
            key=lambda item: sum(u for *_ignored, u in item[1]),
            reverse=True,
        )
        lines: list[str] = []
        used = 0
        for file, symbols in ranked:
            block = [f"{file}:"]
            for name, kind, line, uses in symbols[:8]:
                suffix = f" ({uses} refs)" if uses else ""
                block.append(f"  {kind} {name}:{line}{suffix}")
            chunk = "\n".join(block)
            if used + len(chunk) + 1 > max_chars:
                lines.append("…[map truncated — ask about specific files]")
                break
            lines.append(chunk)
            used += len(chunk) + 1
        return "\n".join(lines) or "No symbols indexed yet."
```

Add the tool:

```python
class RepoMapTool(Tool):
    name = "repo_map"
    description = (
        "Ranked overview of the whole repo: the most-referenced classes and "
        "functions per file, hottest files first. Call this FIRST on a "
        "codebase you haven't explored — it tells you where to look."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, index: SymbolIndex):
        self.index = index

    async def run(self) -> str:
        await self.index.ensure_fresh()
        return await self.index.build_repo_map()
```

Register in `pyrrhon/repl.py` `build_agent` tools list after `DependenciesTool(index)`:

```python
        RepoMapTool(index),
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_repo_map.py tests/test_import_graph.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/ast_index.py pyrrhon/repl.py tests/test_repo_map.py
git commit -m "feat(ast): ranked repo_map tool for codebase orientation"
```

---

## Phase C — Deep subagent harness

### Task 6: Narrate-before-dispatch (spoken text alongside tool calls)

**Files:**
- Modify: `pyrrhon/core/agent/loop.py` (yield gated narration when a reply carries both text and tool calls)
- Test: `tests/test_loop_guards.py` (append)

**Interfaces:**
- Consumes: `Agent.run_turn` from Task 3, `GroundingGate.check`.
- Produces: a `SpeechChunk` per narrated tool round. Voice UX contract: the user hears "let me dig into that…" instead of silence while `think_deeper` (or any slow tool) runs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop_guards.py`:

```python
async def test_narration_alongside_tool_calls_is_spoken():
    fast = FakeLLM([
        LLMReply(
            text="Give me a second — I'm tracing that through the codebase.",
            tool_calls=(ToolCall(id="c1", name="echo", arguments={"text": "x"}),),
        ),
        LLMReply(text="Here is the answer."),
    ])
    agent = Agent(llm=fast, tools=[EchoTool()], system_prompt="p", repo_root=FIXTURE)
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e.text for e in events if isinstance(e, SpeechChunk)]
    assert speech[0].startswith("Give me a second")
    assert speech[-1] == "Here is the answer."


async def test_no_narration_event_when_reply_has_no_text():
    fast = FakeLLM([
        LLMReply(tool_calls=(ToolCall(id="c1", name="echo", arguments={"text": "x"}),)),
        LLMReply(text="answer"),
    ])
    agent = Agent(llm=fast, tools=[EchoTool()], system_prompt="p", repo_root=FIXTURE)
    events = [e async for e in agent.run_turn([], "q")]
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert len(speech) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_loop_guards.py -v`
Expected: the two new tests FAIL (no narration `SpeechChunk` is emitted today).

- [ ] **Step 3: Implement**

In `run_turn` (Task 3 version), insert between the `if not reply.tool_calls:` block and `history.append(assistant_tool_message(reply))`:

```python
            if reply.text:
                # Narration spoken while tools run. It passes the gate too:
                # a fabricated citation must never be spoken, even mid-turn.
                narration = reply.text
                if self.grounding_gate is not None:
                    narration = (await self.grounding_gate.check(narration)).speech_text
                if narration.strip():
                    yield SpeechChunk(text=narration)
```

(The text is already persisted in history by `assistant_tool_message`, so no history change.)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS. Check `tests/test_escalation.py::test_full_turn_escalates_through_think_deeper` still passes (its tool-call reply has `text=None` — no narration event).

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/agent/loop.py tests/test_loop_guards.py
git commit -m "feat(agent): speak narration that accompanies tool calls"
```

### Task 7: `ThinkDeeperTool` becomes a bounded tooled subagent

**Files:**
- Modify: `pyrrhon/core/agent/escalate.py` (subagent loop)
- Modify: `pyrrhon/core/agent/prompts.py` (add `DEEP_AGENT_PROMPT`)
- Test: `tests/test_escalation.py` (extend; keep existing tests green — tool-less mode is unchanged)

**Interfaces:**
- Consumes: `ToolGuard`, `DUPLICATE_NOTE`, `assistant_tool_message` from `pyrrhon.core.agent.guards` (Task 3); `Tool` protocol.
- Produces: `ThinkDeeperTool(deep_llm, tools: list[Tool] | None = None, max_rounds: int = 12)`. With `tools=None` behavior is byte-identical to today (single-shot consultant). With tools, it runs its own loop and returns the report string. Structural recursion guard: the belt never contains `think_deeper` (enforced with a `ValueError`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_escalation.py`:

```python
import pytest

from pyrrhon.core.agent.prompts import DEEP_AGENT_PROMPT
from pyrrhon.core.tools.base import Tool


class FakeRepoTool(Tool):
    name = "read_file"
    description = "fake"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"]}

    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, path: str) -> str:
        self.calls.append({"path": path})
        return f"    1| contents of {path}"


async def test_subagent_investigates_with_its_own_tools():
    repo_tool = FakeRepoTool()
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id="d1", name="read_file", arguments={"path": "app.py"}),
        )),
        LLMReply(text="Report: app.py:1 is the entry point."),
    ])
    tool = ThinkDeeperTool(deep, tools=[repo_tool])
    out = await tool.run(question="entry point?", context="unknown")
    assert out == "Report: app.py:1 is the entry point."
    assert repo_tool.calls == [{"path": "app.py"}]
    # Tooled mode uses the agentic prompt and offers schemas.
    assert deep.calls[0]["messages"][0]["content"] == DEEP_AGENT_PROMPT
    assert deep.calls[0]["tools"] is not None
    # The tool result reached the deep model's context.
    tool_msgs = [m for m in deep.calls[1]["messages"] if m.get("role") == "tool"]
    assert "contents of app.py" in tool_msgs[0]["content"]


async def test_subagent_round_cap_forces_a_report():
    repo_tool = FakeRepoTool()
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id=f"d{i}", name="read_file", arguments={"path": f"f{i}.py"}),
        ))
        for i in range(2)
    ] + [LLMReply(text="Best-effort report.")])
    tool = ThinkDeeperTool(deep, tools=[repo_tool], max_rounds=2)
    out = await tool.run(question="q", context="c")
    assert out == "Best-effort report."
    assert deep.calls[-1]["tools"] is None  # forced report call disables tools


async def test_subagent_duplicate_calls_are_not_rerun():
    repo_tool = FakeRepoTool()
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id="d1", name="read_file", arguments={"path": "same.py"}),
        )),
        LLMReply(tool_calls=(
            ToolCall(id="d2", name="read_file", arguments={"path": "same.py"}),
        )),
        LLMReply(text="report"),
    ])
    tool = ThinkDeeperTool(deep, tools=[repo_tool])
    assert await tool.run(question="q", context="c") == "report"
    assert len(repo_tool.calls) == 1


def test_subagent_refuses_recursive_escalation():
    class Recursive(Tool):
        name = "think_deeper"
        description = "no"
        parameters = {"type": "object", "properties": {}}

        async def run(self) -> str:
            return ""

    with pytest.raises(ValueError):
        ThinkDeeperTool(FakeLLM([]), tools=[Recursive()])


async def test_subagent_tool_failure_returns_error_string():
    deep = FakeLLM([
        LLMReply(tool_calls=(
            ToolCall(id="d1", name="no_such_tool", arguments={}),
        )),
        LLMReply(text="report despite missing tool"),
    ])
    tool = ThinkDeeperTool(deep, tools=[FakeRepoTool()])
    assert await tool.run(question="q", context="c") == "report despite missing tool"
    tool_msgs = [m for m in deep.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs[0]["content"].startswith("ERROR: no tool named")
```

Also update the existing assertion in `test_think_deeper_sends_prompt_question_and_context` — the comment stays true only for tool-less construction; no code change needed there (it constructs `ThinkDeeperTool(deep)` with no tools).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_escalation.py -v`
Expected: new tests FAIL — `TypeError: __init__() got an unexpected keyword argument 'tools'` / `ImportError: cannot import name 'DEEP_AGENT_PROMPT'`

- [ ] **Step 3: Add `DEEP_AGENT_PROMPT` to `pyrrhon/core/agent/prompts.py`**

```python
DEEP_AGENT_PROMPT = """\
You are the deep-reasoning subagent of Pyrrhon, a senior engineer's engineer.
A faster conversational model dispatched you with a hard question and its
notes. You have READ-ONLY tools over the repo: files, grep, glob, symbol
definitions and references, import dependencies, a ranked repo map, and git
history. Investigate yourself — verify the notes, then extend them.

Rules:
- Every tool call must answer a specific open question; never re-request
  what you already have.
- Cite path:line ONLY for locations you saw in tool output or the provided
  notes — never invent locations.
- When you can answer (or your budget runs out), stop calling tools and write
  the report: conclusions first, then evidence with citations, then open
  questions. 400 words maximum — a fast model relays this aloud.
"""
```

- [ ] **Step 4: Rewrite `pyrrhon/core/agent/escalate.py`**

Full new module body (docstring + class):

```python
"""Deep-model escalation: a bounded subagent behind the think_deeper tool.

The fast model stays the low-latency voice and decides when to dispatch
(escalation is a tool, not a router). With `tools`, the deep model runs its
own read-only investigation loop in a FRESH context — isolated from the
conversation history — and returns a compact cited report. Depth is
structurally 1: the belt may not contain think_deeper. Without `tools` it
degrades to the M4 single-shot consultant.

Cancellation: run() is awaited inside Agent.run_turn, which runs inside the
Session's cancellable task — barge-in kills the whole investigation.
"""

from __future__ import annotations

from pyrrhon.core.agent.guards import (
    DUPLICATE_NOTE,
    ToolGuard,
    assistant_tool_message,
)
from pyrrhon.core.agent.prompts import DEEP_AGENT_PROMPT, DEEP_SYSTEM_PROMPT
from pyrrhon.core.tools.base import Tool

DEEP_MAX_ROUNDS = 12

_REPORT_NUDGE = (
    "Investigation budget exhausted. Write your report now from the evidence "
    "above; cite only path:line locations you actually saw."
)


class ThinkDeeperTool(Tool):
    name = "think_deeper"
    description = (
        "Dispatch the deep-reasoning subagent for multi-file architectural "
        "analysis. Pass the question plus everything you already know as "
        "`context` — the subagent verifies and extends it with its own "
        "read-only repo tools and returns a cited report."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The hard question to analyze",
            },
            "context": {
                "type": "string",
                "description": "Code excerpts, path:line locations, and findings gathered so far",
            },
        },
        "required": ["question", "context"],
    }

    def __init__(self, deep_llm, tools: list[Tool] | None = None,
                 max_rounds: int = DEEP_MAX_ROUNDS):
        if any(tool.name == self.name for tool in tools or []):
            raise ValueError("think_deeper must not be in its own tool belt")
        self.deep_llm = deep_llm  # anything with async chat(messages, tools=None)
        self.tools = {tool.name: tool for tool in (tools or [])}
        self.max_rounds = max_rounds

    async def run(self, question: str, context: str) -> str:
        prompt = DEEP_AGENT_PROMPT if self.tools else DEEP_SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"{question}\n\n# Context gathered by the fast model\n\n{context}",
            },
        ]
        schemas = [tool.schema() for tool in self.tools.values()] or None
        guard = ToolGuard()
        try:
            for _ in range(self.max_rounds):
                reply = await self.deep_llm.chat(messages, tools=schemas)
                if not reply.tool_calls:
                    return reply.text or "ERROR: deep model returned no text."
                messages.append(assistant_tool_message(reply))
                for call in reply.tool_calls:
                    if guard.is_duplicate(call.name, call.arguments):
                        result = DUPLICATE_NOTE.format(name=call.name)
                    else:
                        result = guard.clip(
                            await self._run_tool(call.name, call.arguments)
                        )
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
                if guard.exhausted:
                    break
            reply = await self.deep_llm.chat(
                [*messages, {"role": "user", "content": _REPORT_NUDGE}], tools=None
            )
            return reply.text or "ERROR: deep model returned no text."
        except Exception as exc:  # provider/network failure must not kill the turn
            return f"ERROR: deep model call failed: {exc}"

    async def _run_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"ERROR: no tool named '{name}'."
        try:
            return await tool.run(**args)
        except TypeError as exc:
            return f"ERROR: bad arguments for {name}: {exc}"
```

(`asyncio.CancelledError` is a `BaseException` since 3.8, so the bare `except Exception` already lets barge-in cancellation propagate.)

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_escalation.py -v`
Expected: all PASS, including the untouched M4 tests (tool-less path identical).

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent/escalate.py pyrrhon/core/agent/prompts.py tests/test_escalation.py
git commit -m "feat(escalate): think_deeper runs a bounded read-only subagent loop"
```

### Task 8: Wire the subagent tool belt through `build_agent`

**Files:**
- Modify: `pyrrhon/core/agent/loop.py` (`deep_tools` param → `ThinkDeeperTool(deep_llm, tools=deep_tools)`)
- Modify: `pyrrhon/core/agent/prompts.py` (`ESCALATION_NOTE` update)
- Modify: `pyrrhon/repl.py` (`build_agent` composes the belt)
- Test: `tests/test_build_agent_m4.py` (extend)

**Interfaces:**
- Consumes: everything above.
- Produces: `Agent(..., deep_llm=..., deep_tools: list[Tool] | None = None)`; `build_agent` gives the subagent: `read_file`, `grep`, `glob`, `find_symbol`, `find_references`, `list_dependencies`, `repo_map`, `git_log`, `git_blame`, `git_show`. Excluded on purpose: `think_deeper` (recursion), `write_spec` (read-only), `remember` (fast model owns memory), web tools (repo questions stay in the repo), MCP/plugin tools (uncontrolled cost).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_build_agent_m4.py`:

```python
def test_build_agent_gives_the_subagent_a_read_only_belt(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from pyrrhon.repl import build_agent
    from tests.helpers import FakeLLM

    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]))
    deep_tool = agent.tools["think_deeper"]
    belt = set(deep_tool.tools)
    assert {"read_file", "grep", "glob", "find_symbol", "find_references",
            "list_dependencies", "repo_map", "git_log", "git_blame",
            "git_show"} <= belt
    assert "think_deeper" not in belt
    assert "write_spec" not in belt
    assert "web_search" not in belt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_agent_m4.py -v`
Expected: new test FAILS — `AttributeError: 'ThinkDeeperTool' object has no attribute 'tools'` is already fixed by Task 7, so the failure is `assert set() >= {...}` (empty belt).

- [ ] **Step 3: Implement**

`pyrrhon/core/agent/loop.py` — add constructor param after `deep_llm=None`:

```python
        deep_tools: list[Tool] | None = None,
```

and change the registration line:

```python
        if deep_llm is not None:
            deep_tool = ThinkDeeperTool(deep_llm, tools=deep_tools)
```

`pyrrhon/repl.py` — in `build_agent`, before the `Agent(...)` call:

```python
    deep_tools = [
        ReadFileTool(repo_root),
        GrepTool(repo_root),
        GlobTool(repo_root),
        FindSymbolTool(index),
        FindReferencesTool(index),
        DependenciesTool(index),
        RepoMapTool(index),
        GitLogTool(repo_root),
        GitBlameTool(repo_root),
        GitShowTool(repo_root),
    ]
```

and pass `deep_tools=deep_tools` in the `Agent(...)` call.

`pyrrhon/core/agent/prompts.py` — replace `ESCALATION_NOTE` with:

```python
ESCALATION_NOTE = """\
You also have a think_deeper tool backed by a stronger reasoning subagent
with its own read-only repo tools. Dispatch it for multi-file architectural
analysis: "map how X affects Y", impact-of-change questions spanning several
files, or design trade-off evaluations. Pass the question plus what you
already know as `context` — the subagent verifies and extends it itself, so
you don't need to pre-gather everything. In the same reply as the tool call,
say one short sentence telling the user you're digging deeper (it is spoken
while the analysis runs). Do not escalate simple lookups.
"""
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS (`tests/test_escalation.py::test_agent_registers_tool_and_note_only_with_deep_llm` asserts `ESCALATION_NOTE in system_prompt` — still true with the new text).

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/agent/loop.py pyrrhon/core/agent/prompts.py pyrrhon/repl.py tests/test_build_agent_m4.py
git commit -m "feat(escalate): wire read-only tool belt into the deep subagent"
```

---

## Phase D — Provider isolation: STT/TTS registry + local models

### Task 9: Local-friendly LLM providers + voice provider settings

**Files:**
- Modify: `pyrrhon/config/settings.py` (`VoiceSettings` providers; `ProviderConfig.api_key_env` optional; `ollama`/`lmstudio` builtins)
- Modify: `pyrrhon/core/providers/llm.py` (keyless provider path)
- Test: `tests/test_settings.py`, `tests/test_llm_adapter.py` (extend)

**Interfaces:**
- Consumes: existing `Settings` / `create_llm`.
- Produces: `VoiceSettings` gains `stt_provider: str = "groq"`, `tts_provider: str = "openai"`, `tts_model: str | None = None`, `tts_url: str | None = None` (existing fields keep working). `ProviderConfig.api_key_env: str = ""` — empty means "no key needed"; `create_llm` then passes the literal placeholder `"local"` to the SDK (it requires a non-empty string). Builtins: `ollama` → `http://localhost:11434/v1`, `lmstudio` → `http://localhost:1234/v1`, both keyless.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py`:

```python
def test_voice_provider_settings_defaults_and_override(tmp_path):
    from pyrrhon.config.settings import load_settings

    voice = load_settings(tmp_path).voice
    assert voice.stt_provider == "groq"
    assert voice.tts_provider == "openai"
    (tmp_path / ".pyrrhon.toml").write_text(
        '[voice]\ntts_provider = "cartesia"\ntts_voice = "some-voice-id"\n'
        'tts_model = "sonic-2"\nstt_provider = "whisper-local"\n',
        encoding="utf-8",
    )
    voice = load_settings(tmp_path).voice
    assert voice.tts_provider == "cartesia"
    assert voice.tts_model == "sonic-2"
    assert voice.stt_provider == "whisper-local"


def test_local_llm_providers_are_builtin_and_keyless(tmp_path):
    from pyrrhon.config.settings import BUILTIN_PROVIDERS

    assert BUILTIN_PROVIDERS["ollama"].base_url == "http://localhost:11434/v1"
    assert BUILTIN_PROVIDERS["ollama"].api_key_env == ""
    assert BUILTIN_PROVIDERS["lmstudio"].base_url == "http://localhost:1234/v1"
```

Append to `tests/test_llm_adapter.py`:

```python
def test_create_llm_allows_keyless_local_provider(tmp_path, monkeypatch):
    from pyrrhon.config.settings import ModelSlot, Settings
    from pyrrhon.core.providers.llm import create_llm

    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    slot = ModelSlot(provider="ollama", model="qwen3:8b")
    llm = create_llm(slot, Settings())
    assert llm.model == "qwen3:8b"  # no MissingAPIKeyError for keyless providers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_settings.py tests/test_llm_adapter.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'stt_provider'`, `KeyError: 'ollama'`

- [ ] **Step 3: Implement settings**

In `pyrrhon/config/settings.py`, change `ProviderConfig`:

```python
class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str = ""  # empty: provider needs no key (local servers)
```

Add to `BUILTIN_PROVIDERS`:

```python
    "ollama": ProviderConfig(base_url="http://localhost:11434/v1", api_key_env=""),
    "lmstudio": ProviderConfig(base_url="http://localhost:1234/v1", api_key_env=""),
```

Replace `VoiceSettings`:

```python
class VoiceSettings(BaseModel):
    """M3/M8 voice-channel knobs (TOML section [voice]).

    stt_provider: groq | openai | whisper-local
    tts_provider: openai | cartesia | elevenlabs | piper
    tts_voice is provider-specific: an OpenAI voice name ("nova"), a
    Cartesia/ElevenLabs voice id, or ignored (piper).
    """

    stt_provider: str = "groq"
    stt_model: str = "whisper-large-v3-turbo"
    tts_provider: str = "openai"
    tts_model: str | None = None               # provider default when unset
    tts_voice: str = "nova"
    tts_url: str | None = None                 # local TTS server (piper)
    chars_per_sec: float = 15.0                # played-text estimator rate
```

- [ ] **Step 4: Implement the keyless path in `create_llm`**

In `pyrrhon/core/providers/llm.py`, replace the key lookup in `create_llm`:

```python
    provider = settings.provider_for(slot)
    if provider.api_key_env:
        api_key = os.environ.get(provider.api_key_env, "")
        if not api_key:
            raise MissingAPIKeyError(
                f"Set {provider.api_key_env} to use provider '{slot.provider}'."
            )
    else:
        api_key = "local"  # SDK requires non-empty; local servers ignore it
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_settings.py tests/test_llm_adapter.py tests/test_fallback_llm.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/config/settings.py pyrrhon/core/providers/llm.py tests/test_settings.py tests/test_llm_adapter.py
git commit -m "feat(providers): keyless local LLM providers and voice provider settings"
```

### Task 10: STT/TTS factory registry + pipeline rewiring + docs

**Files:**
- Create: `pyrrhon/voice/providers.py`
- Modify: `pyrrhon/voice/pipeline.py` (use factories; `VoiceUnavailableError` moves to providers.py and is re-exported)
- Modify: `README.md` (config examples), `CLAUDE.md` (current-state paragraph)
- Test: `tests/test_voice_providers.py`

**Interfaces:**
- Consumes: `VoiceSettings` from Task 9; Pipecat service classes (lazy-imported).
- Produces: `create_stt(voice: VoiceSettings)`, `create_tts(voice: VoiceSettings)`, `VoiceUnavailableError` (canonical home now `pyrrhon.voice.providers`; `pyrrhon.voice.pipeline.VoiceUnavailableError` keeps working via re-import). Registries: STT `groq` (GROQ_API_KEY), `openai` (OPENAI_API_KEY), `whisper-local` (no key, `pipecat-ai[whisper]`); TTS `openai` (OPENAI_API_KEY), `cartesia` (CARTESIA_API_KEY, `pipecat-ai[cartesia]`), `elevenlabs` (ELEVENLABS_API_KEY, `pipecat-ai[elevenlabs]`), `piper` (no key, local server at `tts_url`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_providers.py`:

```python
"""STT/TTS provider registry: config-driven, lazily imported, degrades cleanly.

These tests never import pipecat service classes for real — unknown
providers and missing keys fail BEFORE any pipecat import happens, which is
exactly the property the tests pin down.
"""

import pytest

from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.providers import VoiceUnavailableError, create_stt, create_tts


def test_unknown_providers_fail_with_the_valid_list():
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="nope"))
    assert "groq" in str(exc.value)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="nope"))
    assert "cartesia" in str(exc.value)


def test_missing_key_degrades_before_importing_pipecat(monkeypatch):
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="cartesia"))
    assert "CARTESIA_API_KEY" in str(exc.value)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="groq"))
    assert "GROQ_API_KEY" in str(exc.value)


def test_pipeline_reexports_error_class():
    from pyrrhon.voice.pipeline import VoiceUnavailableError as reexported

    assert reexported is VoiceUnavailableError
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_voice_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.voice.providers'`

- [ ] **Step 3: Create `pyrrhon/voice/providers.py`**

```python
"""STT/TTS provider registry: mirrors the LLM slot pattern for audio.

Key check happens BEFORE the pipecat import, and each provider's import is
lazy — a missing optional extra degrades to text mode with an actionable
message instead of crashing at import time (M3 error policy).

Latency notes for the config-curious (Coval/CodeSOTA 2026 benchmarks):
OpenAI TTS ~380ms+ to first audio (default only because it needs no new
key); cartesia (~90-190ms) is the recommended real-time choice; elevenlabs
Flash ~75-290ms; piper is local, free, ~35ms on CPU behind its HTTP server.
"""

from __future__ import annotations

import os

from pyrrhon.config.settings import VoiceSettings

STT_PROVIDERS = ("groq", "openai", "whisper-local")
TTS_PROVIDERS = ("openai", "cartesia", "elevenlabs", "piper")


class VoiceUnavailableError(RuntimeError):
    """Voice could not start or died; the caller stays in text mode."""


def _key(env: str, what: str) -> str:
    value = os.environ.get(env, "")
    if not value:
        raise VoiceUnavailableError(f"{what} needs {env} set — staying in text mode.")
    return value


def _import_error(exc: ImportError, extra: str) -> VoiceUnavailableError:
    return VoiceUnavailableError(
        f"Voice dependency missing ({exc}). "
        f'Run: uv add "pipecat-ai[{extra}]" — staying in text mode.'
    )


def create_stt(voice: VoiceSettings):
    provider = voice.stt_provider
    if provider == "groq":
        key = _key("GROQ_API_KEY", "Groq Whisper STT")
        try:
            from pipecat.services.groq.stt import GroqSTTService
        except ImportError as exc:
            raise _import_error(exc, "groq") from exc
        return GroqSTTService(api_key=key, model=voice.stt_model)
    if provider == "openai":
        key = _key("OPENAI_API_KEY", "OpenAI STT")
        try:
            from pipecat.services.openai.stt import OpenAISTTService
        except ImportError as exc:
            raise _import_error(exc, "openai") from exc
        return OpenAISTTService(api_key=key, model=voice.stt_model)
    if provider == "whisper-local":
        try:
            from pipecat.services.whisper.stt import WhisperSTTService
        except ImportError as exc:
            raise _import_error(exc, "whisper") from exc
        return WhisperSTTService()  # faster-whisper, runs locally, no key
    raise VoiceUnavailableError(
        f"Unknown stt_provider '{provider}'. Valid: {', '.join(STT_PROVIDERS)}."
    )


def create_tts(voice: VoiceSettings):
    provider = voice.tts_provider
    if provider == "openai":
        key = _key("OPENAI_API_KEY", "OpenAI TTS")
        try:
            from pipecat.services.openai.tts import OpenAITTSService
        except ImportError as exc:
            raise _import_error(exc, "openai") from exc
        kwargs = {"api_key": key, "voice": voice.tts_voice}
        if voice.tts_model:
            kwargs["model"] = voice.tts_model
        return OpenAITTSService(**kwargs)
    if provider == "cartesia":
        key = _key("CARTESIA_API_KEY", "Cartesia TTS")
        try:
            from pipecat.services.cartesia.tts import CartesiaTTSService
        except ImportError as exc:
            raise _import_error(exc, "cartesia") from exc
        kwargs = {"api_key": key, "voice_id": voice.tts_voice}
        if voice.tts_model:
            kwargs["model"] = voice.tts_model
        return CartesiaTTSService(**kwargs)
    if provider == "elevenlabs":
        key = _key("ELEVENLABS_API_KEY", "ElevenLabs TTS")
        try:
            from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        except ImportError as exc:
            raise _import_error(exc, "elevenlabs") from exc
        kwargs = {"api_key": key, "voice_id": voice.tts_voice}
        if voice.tts_model:
            kwargs["model"] = voice.tts_model
        return ElevenLabsTTSService(**kwargs)
    if provider == "piper":
        try:
            from pipecat.services.piper.tts import PiperTTSService
        except ImportError as exc:
            raise _import_error(exc, "piper") from exc
        return PiperTTSService(base_url=voice.tts_url or "http://localhost:5000")
    raise VoiceUnavailableError(
        f"Unknown tts_provider '{provider}'. Valid: {', '.join(TTS_PROVIDERS)}."
    )
```

**Verification sub-step (implementer):** the exact pipecat import paths and constructor kwargs above were written against Pipecat 1.5.0 conventions but MUST be verified against the installed package before this task is marked done:

Run: `uv run python -c "import pipecat.services.openai.tts, pipecat.services.groq.stt; print('core ok')"`
and for each optional provider you enable while testing manually, e.g. `uv add "pipecat-ai[cartesia]"` then `uv run python -c "from pipecat.services.cartesia.tts import CartesiaTTSService; import inspect; print(inspect.signature(CartesiaTTSService.__init__))"`. Adjust kwargs if the installed signature differs (e.g. `model` vs `model_id`).

- [ ] **Step 4: Rewire `pyrrhon/voice/pipeline.py`**

- Delete the `VoiceUnavailableError` class and `_require_env` helper from `pipeline.py`; add near the top:

```python
from pyrrhon.voice.providers import VoiceUnavailableError, create_stt, create_tts
```

- In `run_voice`, delete the `groq_key = ...` / `openai_key = ...` lines and the `GroqSTTService` / `OpenAITTSService` imports from the lazy-import block, and replace the service construction lines with:

```python
    voice = getattr(settings, "voice", None) or VoiceSettings()
    stt = create_stt(voice)
    tts = create_tts(voice)
    chars_per_sec = voice.chars_per_sec
```

(add `from pyrrhon.config.settings import VoiceSettings` to the module imports; the `stt_model`/`tts_voice` locals disappear — the factories read them from `voice`.)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS — pay attention to `tests/test_voice_pipeline.py` and `tests/test_voice_cmd.py`; if they monkeypatched `_require_env` or the old service classes, update the patch targets to `pyrrhon.voice.providers.create_stt` / `create_tts`.

- [ ] **Step 6: Document the new config**

Append to the README configuration section (adapt heading levels to what's there):

```markdown
### Voice providers

```toml
[voice]
stt_provider = "groq"          # groq | openai | whisper-local (no key, on-device)
tts_provider = "cartesia"      # openai (default) | cartesia | elevenlabs | piper (local)
tts_voice = "<voice-id>"       # OpenAI voice name, or Cartesia/ElevenLabs voice id
tts_model = "sonic-2"          # optional provider-specific model
# tts_url = "http://localhost:5000"   # piper only
```

OpenAI TTS is the zero-setup default; for real-time conversation Cartesia or
ElevenLabs are noticeably snappier (~100-300ms to first audio vs 400ms+).
Local, keyless operation: `stt_provider = "whisper-local"`, `tts_provider =
"piper"`, and a local LLM via `[fast] provider = "ollama"` (or `lmstudio`).

### Context budget

```toml
[context]
budget_tokens = 32000       # estimated tokens before old turns are summarized
keep_last_messages = 8      # recent messages always kept verbatim
```
```

Update the `CLAUDE.md` "Current state" paragraph to mention M8: context compaction + tool guards in the agent loop, import-graph/repo-map tools, tooled think_deeper subagent, and the voice provider registry.

- [ ] **Step 7: Commit**

```bash
git add pyrrhon/voice/providers.py pyrrhon/voice/pipeline.py tests/test_voice_providers.py README.md CLAUDE.md
git commit -m "feat(voice): STT/TTS provider registry with local-model support"
```

---

## Verification (whole milestone)

- [ ] `uv run pytest -v` — full suite green.
- [ ] Manual smoke (needs `GROQ_API_KEY`): `uv run pyrrhon --text .` then ask "give me an overview of this repo" (expect a `repo_map` call), "what depends on pyrrhon/core/session.py?" (expect `list_dependencies`), and "map how a barge-in propagates from the voice pipeline into history" (expect narration + `think_deeper` with sub-investigation, final answer with verified citations).
- [ ] Cost sanity: `/debug-history` after the deep question — previous turns' tool results should be stubs, history well under the budget.

## Out of scope (explicitly parked)

- Vector/embedding retrieval, GraphRAG, external memory services (decision record #1, #2).
- Fire-and-forget async subagent with follow-up speech injection, and nested progress events from inside `think_deeper` — future milestone if narration proves insufficient.
- Non-Python grammars for the import graph (the language pack makes each one a query entry later).
- Persisting compaction summaries to `.pyrrhon/` across sessions.
- Deepgram/AssemblyAI STT entries (add on demand — the registry makes each a ~10-line case).
