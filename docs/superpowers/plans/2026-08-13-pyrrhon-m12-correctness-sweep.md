# Pyrrhon M12 — Correctness Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the nine independent defects found in the 2026-08-13 review — two of which make a shipped feature silently do nothing, one of which corrupts conversation history, and one of which makes the product's signature interaction impossible.

**Architecture:** Each task is one defect, one regression test that fails before the fix, one fix, one commit. Three carry real design decisions: `Agent` takes ownership of `deep_llm` behind a `set_deep_llm()` seam so the live `ThinkDeeperTool` follows a slot change; a stream that fails part-way keeps the heard text and refuses to append a second assistant message beside it; and `classify` learns that an affirmative answer to Pyrrhon's own question is a *resume*, not chit-chat, and needs the tool belt.

**Tech Stack:** Python ≥3.12, uv, pytest (asyncio_mode=auto), httpx, ruff, mypy.

## Global Constraints

- Python `>=3.12`; manage deps only via `uv add` / `uv sync`.
- Run tests with `uv run pytest`; a single test with `uv run pytest path::test_name -v`.
- **Grounding is a hard requirement** (CLAUDE.md): no fix may create a path where an unverified claim reaches a channel.
- **A cloned repo is untrusted input** (M11): no fix may widen what a repo can supply without a grant.
- **History records what was heard, not what was generated** (`session.py:9-11`). Task 2 turns on this rule; do not "simplify" it by dropping the partial text.
- Every task must have a test that FAILS before the fix and PASSES after. A fix without a failing-first test is not done.
- All tests from M11 stay green; `uv run ruff check . && uv run mypy pyrrhon/core` stays clean (CI enforces both from M11 Task 7).
- Match surrounding style: double quotes, `from __future__ import annotations`, docstrings explaining *why*.
- Commit after every task with a conventional-commit message; never `--no-verify`.
- **Parked, do not build:** refactoring `loop.py` (615 lines) into modules, replacing the duck-typed LLM interface with a Protocol, reworking the event dataclasses. Those are worth doing and are not this milestone.

## File Structure

| File | Responsibility |
|---|---|
| `pyrrhon/core/agent/loop.py` (modify) | Own `deep_llm`; seal a partial stream instead of double-appending |
| `pyrrhon/core/agent/turn_type.py` (modify) | Affirmative answers to our own question are `RESUME` and get tools |
| `pyrrhon/commands/builtin.py`, `pyrrhon/commands/settings_cmd.py` (modify) | Call `set_deep_llm` instead of assigning a dead attribute |
| `pyrrhon/core/tools/repo.py` (modify) | Reap ripgrep before reading its returncode |
| `pyrrhon/core/tools/web.py` (modify) | SSRF guard + streamed size cap |
| `pyrrhon/config/credentials.py` (modify) | Create at `0600`; validate env-var names |
| `pyrrhon/core/session.py` (modify) | Rewrite the mode message in place; record compaction time |
| `pyrrhon/voice/providers.py`, `pyrrhon/voice/pipeline.py` (modify) | Close the Piper HTTP session |
| `pyrrhon/core/telemetry.py` (modify) | Delete the never-recorded compaction span |
| `tests/test_escalation.py`, `tests/test_text_streaming.py`, `tests/test_turn_type.py`, `tests/test_repo_tools.py`, `tests/test_web_tools.py`, `tests/test_credentials.py`, `tests/test_session_mode.py`, `tests/test_voice_providers.py`, `tests/test_telemetry.py` (modify) | Regression coverage, one per defect |

---

### Task 1: `/settings llm deep` must actually change the deep model

**Files:**
- Modify: `pyrrhon/core/agent/loop.py:179-219`
- Modify: `pyrrhon/commands/builtin.py:46-52`
- Modify: `pyrrhon/commands/settings_cmd.py:84-90`
- Modify: `pyrrhon/tui/app.py:160-165`
- Test: `tests/test_escalation.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `Agent.deep_llm` (real attribute); `Agent.set_deep_llm(llm) -> None`, which updates the attribute **and** the live `ThinkDeeperTool.deep_llm`.

**Why this is a bug, not a nit:** `settings_cmd.py:89` and `builtin.py:51` assign
`agent.deep_llm`, an attribute `Agent` never defines or reads. `Agent.__init__`
(`loop.py:216-219`) captures the deep model *inside* `ThinkDeeperTool` and drops
the reference. So `/settings llm deep …` replies "saved and **active**", the
status bar changes, and `think_deeper` keeps calling the old model forever.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_escalation.py (append)
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM


async def test_set_deep_llm_changes_what_think_deeper_actually_calls(tmp_path):
    original = FakeLLM([LLMReply(text="from the original deep model")])
    replacement = FakeLLM([LLMReply(text="from the replacement deep model")])
    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=original, home=tmp_path)

    agent.set_deep_llm(replacement)

    report = await agent.tools["think_deeper"].run(question="q", context="c")
    assert report == "from the replacement deep model"
    assert agent.deep_llm is replacement
    assert original.calls == []  # the old model must not be consulted at all
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_escalation.py::test_set_deep_llm_changes_what_think_deeper_actually_calls -v`
Expected: FAIL — `AttributeError: 'Agent' object has no attribute 'set_deep_llm'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/agent/loop.py`, at the end of `Agent.__init__`:

```python
        # Owned, not just consumed. Channels swap the deep slot at runtime
        # (/model deep, /settings llm deep), and the tool captured the model at
        # construction — so before this the swap silently did nothing while the
        # command replied "active". Keep the attribute and the live tool in
        # lockstep through one seam.
        self.deep_llm = deep_llm
        if deep_llm is not None:
            deep_tool = ThinkDeeperTool(deep_llm, tools=deep_tools)
            self.tools[deep_tool.name] = deep_tool
            self.system_prompt = system_prompt + "\n" + ESCALATION_NOTE

    def set_deep_llm(self, llm) -> None:
        """Point escalation at a different model for the rest of the session.

        No-op on the tool when think_deeper was never registered (no deep key
        at build time) — the attribute still updates so the status bar is honest.
        """
        self.deep_llm = llm
        tool = self.tools.get("think_deeper")
        if tool is not None:
            tool.deep_llm = llm
```

In `pyrrhon/commands/builtin.py`, replace `ctx.agent.deep_llm = llm` with:

```python
    ctx.agent.set_deep_llm(llm)
    return f"deep slot is now {provider}/{model}."
```

(also drop the stale "escalation lands in M4" text — M4 landed in July.)

In `pyrrhon/commands/settings_cmd.py:89`, replace `agent.deep_llm = llm` with
`agent.set_deep_llm(llm)`.

In `pyrrhon/tui/app.py:162`, the `getattr` chain now resolves for real; simplify to:

```python
        deep = getattr(self.agent.deep_llm, "model", "= fast")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_escalation.py tests/test_settings_cmd.py tests/test_builtin_commands.py tests/test_tui_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/agent/loop.py pyrrhon/commands/ pyrrhon/tui/app.py tests/test_escalation.py
git commit -m "fix(agent): make the deep-model slot switch reach think_deeper"
```

---

### Task 2: A failed stream must not append a second assistant message

**Files:**
- Modify: `pyrrhon/core/agent/loop.py:286-352,456-522,541-606`
- Test: `tests/test_text_streaming.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `Agent._seal_partial(history: list[dict], live: list[dict]) -> bool`; `_emit_final(..., record: bool = True)`; `_stream_round(..., live: list[dict] | None = None)` which appends its live slot to `live` the moment it is created.
- Constant: `CUT_OFF_MARKER = " …[cut off by a provider error]"`.

**Why:** confirmed by probe. When `llm.stream` raises after emitting chunks, the
live slot written at `loop.py:596-600` stays in history as a finished assistant
message and `_emit_final` appends a second one:

```
assistant: "Here is the first paragraph of my answer.\n\nAnd a second one."
assistant: "This thread got too big for my model's context, even after trimming."
```

Two consecutive assistant turns — which strict OpenAI-compatible endpoints
reject — plus a truncated draft recorded as a completed answer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_text_streaming.py (append)
from pyrrhon.core.agent.loop import CUT_OFF_MARKER, Agent
from pyrrhon.core.providers.llm import ContextLengthExceededError, LLMReply


class StreamThatDiesMidAnswer:
    """Streams two blocks, then the provider rejects the round."""

    async def stream(self, messages, tools=None):
        yield ("text", "Here is the first paragraph.\n\n")
        yield ("text", "And a second one.\n\n")
        raise ContextLengthExceededError("prompt too long")

    async def chat(self, messages, tools=None):
        return LLMReply(text="unused")


async def test_a_dead_stream_leaves_exactly_one_assistant_message(tmp_path):
    agent = Agent(
        llm=StreamThatDiesMidAnswer(), tools=[], system_prompt="s", repo_root=tmp_path
    )
    history: list[dict] = []
    async for _event in agent.run_turn(history, "explain the loop"):
        pass

    assistants = [m for m in history if m["role"] == "assistant"]
    assert len(assistants) == 1, f"expected one assistant turn, got {len(assistants)}"
    # What the user actually heard is preserved, and marked as incomplete.
    assert assistants[0]["content"].startswith("Here is the first paragraph.")
    assert assistants[0]["content"].endswith(CUT_OFF_MARKER)


async def test_no_two_assistant_messages_are_ever_adjacent(tmp_path):
    agent = Agent(
        llm=StreamThatDiesMidAnswer(), tools=[], system_prompt="s", repo_root=tmp_path
    )
    history: list[dict] = []
    async for _event in agent.run_turn(history, "explain the loop"):
        pass
    roles = [m["role"] for m in history]
    assert not any(a == b == "assistant" for a, b in zip(roles, roles[1:]))


async def test_the_user_still_hears_the_error(tmp_path):
    from pyrrhon.core.agent.loop import CONTEXT_FULL_MESSAGE
    from pyrrhon.core.events import SpeechChunk

    agent = Agent(
        llm=StreamThatDiesMidAnswer(), tools=[], system_prompt="s", repo_root=tmp_path
    )
    spoken = [
        event.text
        async for event in agent.run_turn([], "explain the loop")
        if isinstance(event, SpeechChunk)
    ]
    assert CONTEXT_FULL_MESSAGE in spoken
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_text_streaming.py -k dead_stream -v`
Expected: FAIL — `expected one assistant turn, got 2`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/agent/loop.py`, add the constant beside the other messages:

```python
# A streamed answer the round failed part-way through. Distinct from
# session.INTERRUPTED_MARKER, which means the USER cut in: this one means the
# model stopped, and the difference matters when reading a transcript back.
CUT_OFF_MARKER = " …[cut off by a provider error]"
```

Add the sealer as an `Agent` method:

```python
    def _seal_partial(self, history: list[dict], live: list[dict]) -> bool:
        """Close out a streamed answer whose round then failed.

        The text is already on screen or already spoken, so it stays in
        history — that is the "history records what was heard" rule, and it is
        what a barge-in would truncate against. What must NOT happen is
        appending a second assistant message beside it: two adjacent assistant
        turns are rejected by strict chat endpoints, and an error line recorded
        as an answer is a lie about what was said.

        Returns True when a partial was sealed, telling the caller to emit its
        message WITHOUT recording it.
        """
        if not live:
            return False
        slot = live[0]
        if not history or history[-1] is not slot:
            return False
        content = slot.get("content")
        if isinstance(content, str) and content.strip():
            slot["content"] = content + CUT_OFF_MARKER
            return True
        history.pop()  # nothing was ever spoken — drop the empty slot
        return False
```

Thread the live slot out of `_stream_round`. Change its signature to accept
`live: list[dict] | None = None` and, where the slot is created
(`loop.py:597-599`), record it immediately:

```python
                    if slot is None:
                        slot = {"role": "assistant", "content": ""}
                        history.append(slot)
                        if live is not None:
                            live.append(slot)
```

In `_run_turn`, create the list per round and seal in every failure branch:

```python
        for _ in range(self.max_tool_rounds):
            spoken_text: str | None = None
            stream_slot: dict | None = None
            live: list[dict] = []
            round_trace = trace.begin_round()
            try:
                with round_trace.time_llm():
                    if streaming:
                        sink: list = []
                        async for event in self._stream_round(
                            history, schemas, sink, round_trace, live
                        ):
                            yield event
                        reply, spoken_text, stream_slot = sink[0]
                    else:
                        reply = await self.llm.chat(history, tools=schemas)
                        round_trace.mark_ttft()
            except InvalidToolCallError:
                if not nudged_invalid_tool:
                    nudged_invalid_tool = True
                    self._seal_partial(history, live)
                    history.append(
                        {"role": "user", "content": _invalid_tool_nudge(list(self.tools))}
                    )
                    continue
                sealed = self._seal_partial(history, live)
                async for event in self._emit_final(
                    history, TOOL_RETRY_EXHAUSTED_MESSAGE, trace, streaming,
                    record=not sealed,
                ):
                    yield event
                return
            except ContextLengthExceededError:
                sealed = self._seal_partial(history, live)
                if context_recoveries < MAX_CONTEXT_RECOVERIES:
                    # ... unchanged body ...
                    if elided or summarized:
                        continue
                async for event in self._emit_final(
                    history, CONTEXT_FULL_MESSAGE, trace, streaming, record=not sealed
                ):
                    yield event
                return
            except Exception as exc:
                logger.warning("llm.chat failed mid-turn: %s: %s", type(exc).__name__, exc)
                sealed = self._seal_partial(history, live)
                async for event in self._emit_final(
                    history, PROVIDER_ERROR_MESSAGE, trace, streaming, record=not sealed
                ):
                    yield event
                return
```

Add the `record` parameter to `_emit_final` (default `True`) and guard **both**
`history.append` sites in it:

```python
    async def _emit_final(
        self,
        history: list[dict],
        text: str,
        trace: TurnTrace | None = None,
        streaming: bool = False,
        record: bool = True,
    ) -> AsyncIterator[Event]:
        """...

        `record=False` is used when a streamed partial answer has already been
        sealed into history: the message still reaches the user, but it is not
        recorded as a separate assistant turn.
        """
        ...
        if self.grounding_gate is None:
            if record:
                history.append({"role": "assistant", "content": text})
            yield SpeechChunk(text=text)
            ...
        ...
        if record:
            history.append({"role": "assistant", "content": gated.speech_text})
        yield SpeechChunk(text=gated.speech_text)
```

**Note on the context-recovery path:** sealing before the `continue` is
deliberate. The retry re-runs the round with a fresh slot, so the previous
partial must already be a closed message or the next stream appends into a
history that ends with a live, still-growing one.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_text_streaming.py tests/test_context_recovery.py tests/test_voice_streaming.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent/loop.py tests/test_text_streaming.py
git commit -m "fix(loop): seal a cut-off stream instead of appending a second assistant turn"
```

---

### Task 3: "Yes" to Pyrrhon's own offer is a resume, and needs tools

**Files:**
- Modify: `pyrrhon/core/agent/turn_type.py:23-101`
- Test: `tests/test_turn_type.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `RESUME = "resume"` constant; `needs_tools(RESUME) is True`.

**Why:** `VOICE_STYLE` (`prompts.py:76-79`) instructs the model to end turns by
offering the next thread and, "when they say yes, explain it." Confirmed by
probe, every natural acceptance currently has the belt withheld:

```
'yes'                      -> social              tools=False
'yes please'               -> ambiguous_followup  tools=False
'yeah do that'             -> ambiguous_followup  tools=False
'sure, walk me through it' -> ambiguous_followup  tools=False
```

So the one-hop-at-a-time walkthrough the product is designed around cannot
happen: Pyrrhon offers to trace something, the user agrees, and the model has
no tools to trace it with. The M10 token saving is kept for genuinely social
turns and given up exactly where the product needs the repo.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_turn_type.py (append)
import pytest

from pyrrhon.core.agent.turn_type import RESUME, SOCIAL, classify, needs_tools

ASKED = [{"role": "assistant", "content": "Want me to trace what calls the tool loop?"}]


@pytest.mark.parametrize(
    "reply",
    ["yes", "yes please", "yeah do that", "sure, walk me through it", "do it",
     "go ahead", "yep", "okay do that"],
)
def test_accepting_our_own_offer_gets_the_tool_belt(reply):
    assert classify(reply, ASKED) == RESUME
    assert needs_tools(RESUME) is True


@pytest.mark.parametrize("reply", ["no", "no thanks", "not now", "thanks", "nah"])
def test_declining_our_own_offer_stays_social(reply):
    assert classify(reply, ASKED) == SOCIAL
    assert needs_tools(SOCIAL) is False


def test_a_bare_yes_with_no_question_behind_it_is_still_social():
    assert classify("yes", [{"role": "assistant", "content": "That is the loop."}]) == SOCIAL


def test_a_greeting_is_social_even_after_a_question():
    assert classify("hi", ASKED) == SOCIAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_turn_type.py -v`
Expected: FAIL — `ImportError: cannot import name 'RESUME'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/agent/turn_type.py`:

```python
SOCIAL = "social"
AMBIGUOUS_FOLLOWUP = "ambiguous_followup"
REPO_QUESTION = "repo_question"
RESUME = "resume"

# An affirmative answer to a question WE asked. Anchored at the start so
# "yes, but what about the gate?" still reads as affirmative; the word after
# it does not have to be understood, because the belt is the safe outcome.
_AFFIRMATIVE_RE = re.compile(
    r"^(yes|yeah|yep|yup|ya|sure|ok|okay|k|please|go on|go ahead|carry on|"
    r"continue|do it|do that|explain|tell me|show me|sounds good|why not|"
    r"absolutely|definitely|of course|alright|let's)\b",
    re.IGNORECASE,
)

# Declining or closing. Checked FIRST, because "no thanks" starts with a word
# that is not in the affirmative list but must never fall through to it.
_DECLINING_RE = re.compile(
    r"^(no|nope|nah|not now|not really|later|maybe later|thanks|thank you|"
    r"ta|cheers|stop|that's all|thats all|nevermind|never mind)\b",
    re.IGNORECASE,
)

# An affirmative longer than this is carrying its own question ("yes but how
# does the gate handle a missing file") and is classified as a repo question
# on its own merits, which also gets the belt.
MAX_RESUME_WORDS = 8
```

Rewrite `classify`:

```python
def classify(user_text: str, history: list[dict] | None = None) -> str:
    """Return SOCIAL, RESUME, AMBIGUOUS_FOLLOWUP, or REPO_QUESTION.

    A reply to Pyrrhon's OWN question is classified first, because that is the
    case the rest of the rules get wrong. VOICE_STYLE tells the model to offer
    the next thread and explain it when the user agrees; withholding the belt
    on "yes please" makes that instruction impossible to follow, and an answer
    given without repo access is exactly the ungrounded failure the gate cannot
    catch (it verifies citations that appear, not claims that cite nothing).
    """
    text = (user_text or "").strip()
    if not text:
        return SOCIAL
    words = text.split()

    if _last_assistant_asked(history):
        if _DECLINING_RE.match(text):
            return SOCIAL
        if len(words) <= MAX_RESUME_WORDS and _AFFIRMATIVE_RE.match(text):
            return RESUME

    if len(words) <= MAX_SOCIAL_WORDS and _SOCIAL_RE.match(text):
        return SOCIAL

    if (
        len(words) <= MAX_FOLLOWUP_WORDS
        and not _CODE_HINT_RE.search(text)
        and _last_assistant_asked(history)
    ):
        return AMBIGUOUS_FOLLOWUP

    return REPO_QUESTION


def needs_tools(turn_type: str) -> bool:
    return turn_type in (REPO_QUESTION, RESUME)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_turn_type.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/agent/turn_type.py tests/test_turn_type.py
git commit -m "fix(turn-type): an affirmative reply to our own question is a resume, and gets tools"
```

---

### Task 4: Reap ripgrep before reading its exit code

**Files:**
- Modify: `pyrrhon/core/tools/repo.py:274-310`
- Test: `tests/test_repo_tools.py` (append)

**Interfaces:**
- Consumes: nothing. Produces: no new symbols.

**Why:** the `finally` at `repo.py:295-298` kills the process whenever
`returncode is None` — which includes a clean stdout EOF, before the transport
has reaped the child. The branch below then reads a kill signal and returns
`"ERROR: grep failed."` for a search that worked. Race-dependent, so it surfaces
as an intermittent field report rather than a test failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repo_tools.py (append)
import pytest

from pyrrhon.core.tools.repo import GrepTool, _ripgrep


@pytest.mark.skipif(_ripgrep() is None, reason="ripgrep not installed")
async def test_repeated_greps_never_report_a_spurious_failure(tmp_path):
    """The returncode race is timing-dependent: run it enough times that a
    kill-before-reap would show up at least once."""
    (tmp_path / "a.py").write_text("needle = 1\n", encoding="utf-8")
    tool = GrepTool(tmp_path)
    for _ in range(25):
        result = await tool.run(pattern="needle")
        assert "ERROR" not in result, result
        assert "a.py:1:" in result


@pytest.mark.skipif(_ripgrep() is None, reason="ripgrep not installed")
async def test_a_genuinely_bad_regex_still_reports_an_error(tmp_path):
    result = await GrepTool(tmp_path).run(pattern="(unclosed")
    assert "ERROR" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repo_tools.py -k spurious -v`
Expected: FAIL intermittently — `ERROR: grep failed.` on at least one iteration.
If it passes on this machine, the race still exists; proceed with the fix and
rely on the second test to prove error reporting is intact.

- [ ] **Step 3: Write minimal implementation**

Replace the `finally` block and the code that follows it:

```python
        finally:
            # Kill ONLY when we walked away early; then reap unconditionally.
            # returncode stays None until the child is waited on, so the old
            # `if returncode is None: kill()` fired on a clean EOF too and the
            # branch below read a kill signal as a failed search.
            if capped and proc.returncode is None:
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()

        if capped:
            hits.append("(truncated)")
            return "\n".join(hits)

        stderr = await proc.stderr.read()
        if proc.returncode == 1:
            return "No matches."  # rg's documented "no matches" exit code
        if proc.returncode not in (0, 1):
            detail = stderr.decode("utf-8", errors="replace").strip()
            return f"ERROR: bad regex: {detail}" if detail else "ERROR: grep failed."
        return "\n".join(hits) or "No matches."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repo_tools.py tests/test_safety.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/repo.py tests/test_repo_tools.py
git commit -m "fix(grep): reap ripgrep before reading its exit code"
```

---

### Task 5: `web_fetch` needs an SSRF guard and a real size cap

**Files:**
- Modify: `pyrrhon/core/tools/web.py:62-95`
- Test: `tests/test_web_tools.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `is_public_host(host: str) -> bool`; `MAX_REDIRECTS = 3`; `MAX_FETCH_BYTES = 2_000_000`.

**Why:** the URL is chosen by a model that reads repo content, and repo content
is untrusted (M11). Today `web_fetch` will happily retrieve
`http://169.254.169.254/latest/meta-data/` (cloud credentials),
`http://localhost:8080/admin`, or any RFC1918 address, and follows redirects to
them. Separately `response.text` materialises the whole body before
`MAX_FETCH_CHARS` trims it, so a large response is an OOM rather than a
truncation.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_tools.py (append)
import pytest

from pyrrhon.core.tools.web import WebFetchTool, is_public_host


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.1.1", "::1"],
)
def test_internal_hosts_are_not_public(host):
    assert is_public_host(host) is False


async def test_fetching_the_cloud_metadata_endpoint_is_refused():
    result = await WebFetchTool().run(url="http://169.254.169.254/latest/meta-data/")
    assert "ERROR" in result
    assert "internal" in result.lower()


async def test_fetching_localhost_is_refused():
    result = await WebFetchTool().run(url="http://localhost:8080/admin")
    assert "ERROR" in result


async def test_a_redirect_into_the_internal_network_is_refused(respx_mock):
    respx_mock.get("https://example.com/start").respond(
        302, headers={"location": "http://169.254.169.254/"}
    )
    result = await WebFetchTool().run(url="https://example.com/start")
    assert "ERROR" in result


async def test_an_oversized_body_is_truncated_not_loaded_whole(respx_mock):
    respx_mock.get("https://example.com/big").respond(
        200, text="x" * 5_000_000, headers={"content-type": "text/plain"}
    )
    result = await WebFetchTool().run(url="https://example.com/big")
    assert result.endswith("(truncated)")
    assert len(result) < 20_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_public_host'`

- [ ] **Step 3: Write minimal implementation**

Rewrite `WebFetchTool.run` in `pyrrhon/core/tools/web.py`:

```python
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

MAX_FETCH_BYTES = 2_000_000  # hard stop before decoding — an OOM guard
MAX_REDIRECTS = 3
INTERNAL_REFUSAL = (
    "ERROR: refusing to fetch an internal address. web_fetch reaches the public "
    "web only — loopback, private, link-local and reserved ranges are blocked."
)


def is_public_host(host: str) -> bool:
    """True only if every address `host` resolves to is publicly routable.

    Every address, not the first: a hostname an attacker controls can resolve
    to one public and one internal address, and picking either at connect time
    would be a coin flip. DNS is a blocking call, so callers run this in a
    worker thread.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for *_unused, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return bool(infos)
```

```python
    async def run(self, url: str) -> str:
        # Redirects are followed BY HAND: httpx's follow_redirects would chase
        # a 302 into the internal network after the first host passed the check.
        for _hop in range(MAX_REDIRECTS + 1):
            if not url.startswith(("http://", "https://")):
                return f"ERROR: only http(s) URLs are supported, got '{url}'."
            host = urlparse(url).hostname or ""
            if not await asyncio.to_thread(is_public_host, host):
                return INTERNAL_REFUSAL
            try:
                async with httpx.AsyncClient(
                    follow_redirects=False, timeout=FETCH_TIMEOUT_SECONDS
                ) as client:
                    async with client.stream("GET", url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                return f"ERROR: HTTP {response.status_code} for {url}"
                            url = urljoin(url, location)
                            continue
                        if response.status_code >= 400:
                            return f"ERROR: HTTP {response.status_code} for {url}"
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) >= MAX_FETCH_BYTES:
                                break
                        content_type = response.headers.get("content-type", "")
            except httpx.HTTPError as exc:
                return f"ERROR: fetch failed: {exc}"
            text = body.decode("utf-8", errors="replace")
            if "html" in content_type:
                text = await asyncio.to_thread(_strip_html, text)
            text = text.strip()
            if len(text) > MAX_FETCH_CHARS:
                text = text[:MAX_FETCH_CHARS] + "\n(truncated)"
            return text or "(empty page)"
        return f"ERROR: too many redirects (>{MAX_REDIRECTS}) for {url}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_web_tools.py -v`
Expected: PASS

- [ ] **Step 5: Pin it as a safety invariant**

Add to `tests/test_safety.py`, beside the existing fences:

```python
async def test_web_fetch_refuses_internal_addresses():
    """A model that reads an untrusted repo picks the URL. The metadata
    endpoint must never be reachable through a Pyrrhon tool."""
    from pyrrhon.core.tools.web import WebFetchTool

    for evil in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "http://[::1]/",
    ):
        assert "ERROR" in await WebFetchTool().run(url=evil)
```

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/tools/web.py tests/test_web_tools.py tests/test_safety.py
git commit -m "fix(web): block SSRF targets and cap the response before decoding"
```

---

### Task 6: Create `credentials.toml` at `0600`, and validate key names

**Files:**
- Modify: `pyrrhon/config/credentials.py:31-43`
- Test: `tests/test_credentials.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `save_credentials` raises `ValueError` on a non-identifier env name; file created with mode `0600` and never widened.

**Why:** the file is written with default permissions and chmod'd afterwards
(`credentials.py:38-42`), so there is a window where a key file is
group/world-readable. Separately the writer interpolates `f"{name} = …"`
unquoted, so a name that is not a bare TOML key produces a file that
`read_credentials` can no longer parse — silently losing every stored key.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_credentials.py (append)
import os
import sys

import pytest

from pyrrhon.config.credentials import credentials_path, read_credentials, save_credentials


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_the_file_is_never_world_readable_even_for_an_instant(tmp_path):
    save_credentials({"GROQ_API_KEY": "sk-test"}, home=tmp_path)
    mode = os.stat(credentials_path(tmp_path)).st_mode & 0o777
    assert mode == 0o600


def test_a_key_name_that_would_corrupt_the_file_is_rejected(tmp_path):
    save_credentials({"GROQ_API_KEY": "sk-good"}, home=tmp_path)
    with pytest.raises(ValueError, match="not a valid environment variable name"):
        save_credentials({"bad name = x": "sk-evil"}, home=tmp_path)
    # The good key survives — a rejected write must not damage the store.
    assert read_credentials(tmp_path) == {"GROQ_API_KEY": "sk-good"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_credentials.py -v`
Expected: FAIL — no `ValueError` is raised; on POSIX the mode assertion may also fail.

- [ ] **Step 3: Write minimal implementation**

```python
import os
import re

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def save_credentials(updates: dict[str, str], home: Path | None = None) -> Path:
    """Merge `updates` into the store, writing owner-only from the first byte.

    os.open with 0o600 rather than write_text-then-chmod: the old order left a
    window where a freshly created key file carried the process umask.
    """
    for name in updates:
        if not _ENV_NAME_RE.match(name):
            raise ValueError(
                f"'{name}' is not a valid environment variable name; "
                "nothing was written."
            )
    path = credentials_path(home)
    merged = {**read_credentials(home), **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Pyrrhon API keys — managed by `pyrrhon --setup`. Env vars win.", "[keys]"]
    lines += [f"{name} = {json.dumps(value)}" for name, value in sorted(merged.items())]
    body = "\n".join(lines) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
    try:
        path.chmod(0o600)  # tighten an already-existing file O_CREAT left alone
    except OSError:
        pass  # Windows: chmod is limited; the profile dir is already per-user
    return path
```

In `pyrrhon/commands/settings_cmd.py`, `_set_key` must surface the new error
rather than crash the channel:

```python
    try:
        save_credentials({env: value})
    except ValueError as exc:
        return f"ERROR: {exc}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_credentials.py tests/test_settings_cmd.py tests/test_wizard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/config/credentials.py pyrrhon/commands/settings_cmd.py tests/test_credentials.py
git commit -m "fix(credentials): create owner-only from the first byte; reject unwritable key names"
```

---

### Task 7: `/mode` must not grow history forever

**Files:**
- Modify: `pyrrhon/core/session.py:28-86`
- Test: `tests/test_session_mode.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `MODE_PREFIX = "[mode]\n"`; `Session.set_mode` rewrites the existing mode message in place.

**Why:** `set_mode` appends a system message on every switch (`session.py:86`),
and `maybe_summarize` deliberately preserves system messages inside the
compacted span (`context.py:153`). So mode messages accumulate without bound
and are immune to the one mechanism that would otherwise trim them — a slow
leak straight into the context window, in a session where toggling modes is a
normal thing to do.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_mode.py (append)
from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.session import MODE_PREFIX, Session
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM


def _mode_messages(history):
    return [
        m for m in history
        if m.get("role") == "system" and str(m.get("content", "")).startswith(MODE_PREFIX)
    ]


def test_toggling_modes_never_adds_a_second_mode_message(tmp_path):
    session = Session(build_agent(tmp_path, llm=FakeLLM([]), home=tmp_path))
    for _ in range(10):
        session.set_mode("design")
        session.set_mode("understand")
    assert len(_mode_messages(session.history)) == 1


def test_the_surviving_mode_message_reflects_the_current_mode(tmp_path):
    session = Session(build_agent(tmp_path, llm=FakeLLM([]), home=tmp_path))
    session.set_mode("design")
    session.set_mode("understand")
    session.set_mode("design")
    assert session.mode == "design"
    assert DESIGN_PROMPT in _mode_messages(session.history)[0]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_mode.py -v`
Expected: FAIL — `assert 20 == 1`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/session.py`:

```python
# Tags the one system message that carries the current mode. A marker rather
# than a remembered index: maybe_summarize splices history[1:split], so any
# index we cached would be stale after the first compaction.
MODE_PREFIX = "[mode]\n"
```

```python
    def set_mode(self, mode: str) -> None:
        """Switch understand <-> design by REWRITING one layered system message.

        The base teaching prompt from turn one always stays underneath. Exactly
        one mode message ever exists: appending per switch grew history without
        bound, and system messages are deliberately preserved by
        maybe_summarize (context.py:153), so nothing would ever have trimmed
        them.
        """
        if mode not in VALID_MODES:
            raise ValueError(
                f"Unknown mode '{mode}'. Valid modes: {', '.join(sorted(VALID_MODES))}."
            )
        if mode == self.mode:
            return
        if not self.history:
            self.history.append(
                {"role": "system", "content": self.agent.system_prompt}
            )
        self.mode = mode
        self.agent.mode = mode
        body = DESIGN_PROMPT if mode == "design" else UNDERSTAND_MARKER
        content = MODE_PREFIX + body
        for message in self.history:
            if message.get("role") == "system" and str(
                message.get("content", "")
            ).startswith(MODE_PREFIX):
                message["content"] = content
                return
        self.history.append({"role": "system", "content": content})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_mode.py tests/test_mode_command.py tests/test_design_session_e2e.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/session.py tests/test_session_mode.py
git commit -m "fix(session): rewrite the mode message in place instead of appending one per switch"
```

---

### Task 8: Close the Piper HTTP session

**Files:**
- Modify: `pyrrhon/voice/providers.py:201-223`
- Modify: `pyrrhon/voice/pipeline.py:50-116`
- Test: `tests/test_voice_providers.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `close_voice_service(service) -> Awaitable[None]` in `pyrrhon/voice/providers.py`, which closes any `aiohttp` session the factory attached.

**Why:** `providers.py:211` constructs `aiohttp.ClientSession()` inline and
nothing ever closes it. Every `/voice on` with Piper HTTP mode leaks a session
and its connector, and aiohttp prints an "Unclosed client session" warning into
the log at interpreter shutdown.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voice_providers.py (append)
import sys
import types

from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.providers import close_voice_service, create_tts


def test_piper_http_session_is_attached_and_closable(monkeypatch):
    closed: list[bool] = []

    class FakeSession:
        async def close(self):
            closed.append(True)

    class FakePiperHttp:
        def __init__(self, base_url, aiohttp_session):
            self.base_url = base_url
            self.session = aiohttp_session

    module = types.ModuleType("pipecat.services.piper.tts")
    module.PiperHttpTTSService = FakePiperHttp
    monkeypatch.setitem(sys.modules, "pipecat.services.piper.tts", module)
    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = FakeSession
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

    service = create_tts(VoiceSettings(tts_provider="piper", tts_url="http://localhost:5000"))
    assert getattr(service, "_pyrrhon_session", None) is not None

    import asyncio

    asyncio.run(close_voice_service(service))
    assert closed == [True]


def test_closing_a_service_with_no_session_is_a_noop():
    import asyncio

    asyncio.run(close_voice_service(object()))  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_providers.py -k piper -v`
Expected: FAIL — `ImportError: cannot import name 'close_voice_service'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/voice/providers.py`, replace the Piper HTTP branch:

```python
        if voice.tts_url:
            # Explicit server mode: talk to a running `piper --http`.
            try:
                from pipecat.services.piper.tts import PiperHttpTTSService
            except ImportError as exc:
                raise _import_error(exc, "piper") from exc
            import aiohttp

            session = aiohttp.ClientSession()
            service = PiperHttpTTSService(
                base_url=voice.tts_url, aiohttp_session=session
            )
            # Stashed so the pipeline can close it on teardown. Pipecat does not
            # own a session it was handed, so without this every /voice on leaks
            # one plus its connector.
            service._pyrrhon_session = session
            return service
```

Add at module level:

```python
async def close_voice_service(service: object) -> None:
    """Close any resource a factory attached to `service`. Safe on anything."""
    session = getattr(service, "_pyrrhon_session", None)
    if session is None:
        return
    try:
        await session.close()
    except Exception:  # teardown must never mask the reason we are tearing down
        pass
```

In `pyrrhon/voice/pipeline.py`, wrap the runner:

```python
    with speech_path(session):
        try:
            await runner.run(task)
        except Exception as exc:  # no mic / device died / provider hiccup
            raise VoiceUnavailableError(
                f"Voice pipeline failed ({exc}) — staying in text mode."
            ) from exc
        finally:
            # Runs on /voice off (CancelledError) too — that is the path that
            # was leaking, since toggling voice is normal and repeated.
            await close_voice_service(tts)
            await close_voice_service(stt)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_voice_providers.py tests/test_voice_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/voice/providers.py pyrrhon/voice/pipeline.py tests/test_voice_providers.py
git commit -m "fix(voice): close the aiohttp session Piper HTTP mode was leaking"
```

---

### Task 9: Delete the telemetry that never records, measure the one that should

**Files:**
- Modify: `pyrrhon/core/telemetry.py:139-252`
- Modify: `pyrrhon/core/session.py:169-180`
- Test: `tests/test_telemetry.py`, `tests/test_background_compaction.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `Session.last_compaction_ms: float | None`. Removes `TurnTrace.time_compaction` and the `compaction_ms` key from `TurnTrace.as_dict()`.

**Why:** `TurnTrace.time_compaction` (`telemetry.py:174`) has no caller —
compaction moved to `Session._compact` in M10 and never calls it — so
`as_dict()` publishes a permanent `0.0` that reads like a measurement. A metric
that is structurally always zero is worse than no metric: the latency harness
records it into every baseline.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telemetry.py (append)
from pyrrhon.core.telemetry import TurnTrace


def test_the_trace_does_not_publish_a_metric_nothing_records():
    assert "compaction_ms" not in TurnTrace().as_dict()
    assert not hasattr(TurnTrace, "time_compaction")
```

```python
# tests/test_background_compaction.py (append)
from pyrrhon.core.session import Session


async def test_background_compaction_records_its_duration(tmp_path):
    """Compaction happens off the turn, so it belongs on the Session, not on a
    TurnTrace that has already been finished and published."""
    session = _session_over_budget(tmp_path)  # existing helper in this module
    session._schedule_compaction()
    await session._compaction
    assert session.last_compaction_ms is not None
    assert session.last_compaction_ms >= 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_telemetry.py tests/test_background_compaction.py -v`
Expected: FAIL — `compaction_ms` is present; `last_compaction_ms` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/telemetry.py`, delete the `compaction_ms` field, the
`time_compaction` context manager, and the `"compaction_ms"` entry in
`as_dict()`.

In `pyrrhon/core/session.py`, add the attribute in `__init__`:

```python
        # Duration of the last background compaction. Lives here, not on
        # TurnTrace: compaction runs AFTER the turn whose trace was already
        # finished and published, so recording it there produced a metric that
        # was structurally always zero.
        self.last_compaction_ms: float | None = None
```

and time it in `_compact`:

```python
    async def _compact(self, budget: int) -> None:
        started = time.perf_counter()
        try:
            await maybe_summarize(
                self.history,
                self.agent.llm,
                budget,
                keep_last=self.agent.context_keep_last,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # never let an optimization kill the session
            logger.debug("background compaction failed", exc_info=True)
        finally:
            self.last_compaction_ms = (time.perf_counter() - started) * 1000.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_telemetry.py tests/test_background_compaction.py tests/test_latency.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/telemetry.py pyrrhon/core/session.py tests/
git commit -m "fix(telemetry): drop the never-recorded compaction span; time it on the session instead"
```

---

### Task 10: Three small ones — command collisions, repeated hedges, install weight

**Files:**
- Modify: `pyrrhon/commands/registry.py:49-56`
- Modify: `pyrrhon/core/agent/loop.py` (`_stream_round`)
- Modify: `pyproject.toml`
- Test: `tests/test_command_registry.py`, `tests/test_voice_streaming.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `command()` logs a warning on re-registration; `_stream_round` suppresses a hedge it has already spoken this turn; `pipecat-ai[local]` moves to an optional extra.

**Why these three together:** each is a few lines, none deserves its own review
cycle, and all three are user-visible rough edges rather than internal tidying.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_command_registry.py (append)
import logging

from pyrrhon.commands.registry import command


def test_re_registering_a_command_name_warns(caplog):
    """Tools warn on collision (repl.py:166); commands silently overwrote, so a
    plugin could replace /settings or /help with nothing in the log."""
    with caplog.at_level(logging.WARNING, logger="pyrrhon.commands"):

        @command("collide-probe", "first")
        def _first(args, ctx):
            return "first"

        @command("collide-probe", "second")
        def _second(args, ctx):
            return "second"

    assert any("collide-probe" in record.message for record in caplog.records)
```

```python
# tests/test_voice_streaming.py (append)
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.grounding.gate import HEDGE, GroundingGate
from pyrrhon.core.providers.llm import LLMReply


class ThreeBadCitations:
    async def stream(self, messages, tools=None):
        yield ("text", "It starts at ghost/one.py:1. ")
        yield ("text", "Then ghost/two.py:2. ")
        yield ("text", "Finally ghost/three.py:3. ")
        yield ("reply", LLMReply(text="…"))


async def test_the_hedge_is_spoken_once_per_turn_not_once_per_sentence(tmp_path):
    agent = Agent(
        llm=ThreeBadCitations(),
        tools=[],
        system_prompt="s",
        repo_root=tmp_path,
        grounding_gate=GroundingGate(tmp_path),
        voice_active=True,
    )
    spoken = " ".join(
        e.text async for e in agent.run_turn([], "where?") if isinstance(e, SpeechChunk)
    )
    assert spoken.count(HEDGE) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_command_registry.py tests/test_voice_streaming.py -v`
Expected: FAIL — no warning is logged; the hedge appears three times.

- [ ] **Step 3: Write minimal implementations**

Registry warning (`pyrrhon/commands/registry.py`):

```python
log = logging.getLogger("pyrrhon.commands")


def command(name: str, help_text: str):
    """Register a slash command. Handler: (args, ctx) -> response string."""

    def register(fn: Callable[[str, CommandContext], str]):
        if name in _COMMANDS:
            # Last registration still wins — a plugin overriding a builtin is a
            # legitimate use. But it must not be silent: tools already warn on
            # collision (repl.py:166) and commands are the more surprising case,
            # since /settings and /help are how the user recovers from trouble.
            log.warning(
                "command /%s re-registered; %s replaces %s",
                name, getattr(fn, "__qualname__", fn), _COMMANDS[name].handler,
            )
        _COMMANDS[name] = Command(name=name, help_text=help_text, handler=fn)
        return fn

    return register
```

Hedge suppression, in `_stream_round`. The gate is per sentence, so a three-bad-
citation answer currently ends three sentences with the same apology:

```python
        buffer = ""
        spoken: list[str] = []
        slot: dict | None = None
        hedged: set[str] = set()  # hedges already spoken this turn
```

```python
            for chunk in chunks:
                speech, citations = await self._gate_sentence(chunk, round_trace)
                # Each sentence is gated independently, so a turn with several
                # bad citations repeats one apology. Say it once: the FIRST
                # sentence carrying it keeps it, later ones are trimmed. The
                # information is identical; the repetition is just noise, and
                # aloud it sounds broken.
                for hedge in (HEDGE, LINE_HEDGE):
                    if hedge in speech:
                        if hedge in hedged:
                            speech = speech.replace(hedge, "").strip()
                        else:
                            hedged.add(hedge)
```

(import `HEDGE` and `LINE_HEDGE` from `pyrrhon.core.grounding.gate`)

Install weight (`pyproject.toml`). `pipecat-ai[local]` pulls PyAudio and the
whole audio stack, but `voice/pipeline.py:67-81` and `voice/providers.py:5-8`
both document the local extra as *optional*, with a degrade path that is
currently unreachable because the dependency is mandatory:

```toml
dependencies = [
    # ... unchanged ...
    "pipecat-ai[groq,openai,silero]>=1.5.0",
]

[project.optional-dependencies]
# The audio stack (PyAudio et al). Text and TUI channels never import it, and
# voice/providers.py degrades with an actionable message when it is missing —
# a path that could not be reached while this was a hard dependency.
voice = ["pipecat-ai[local]>=1.5.0"]
```

Update `README.md` and `CLAUDE.md`: voice needs `uv sync --extra voice`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_command_registry.py tests/test_voice_streaming.py tests/test_voice_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Prove the degrade path now actually works**

In a scratch venv without the extra, run `uv run pyrrhon --text .` and confirm
it starts, then that `/voice on` returns the actionable "voice dependencies
missing" message rather than an ImportError traceback.

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/commands/registry.py pyrrhon/core/agent/loop.py pyproject.toml README.md CLAUDE.md tests/
git commit -m "fix: warn on command collisions, speak each hedge once, make the audio stack optional"
```

---

## Verification

Before opening the PR:

- [ ] `uv run pytest -q` — all green
- [ ] `uv run ruff check . && uv run mypy pyrrhon/core` — clean
- [ ] Every task's test was confirmed RED before its fix. If any task skipped
      that step, redo it — a green-only test proves nothing about the defect.
- [ ] Manual: `/settings llm deep <other-provider>/<model>`, then ask a question
      that escalates, and confirm from `/debug-history` that the new model answered.
- [ ] Manual: ask a question, let Pyrrhon offer the next thread, answer "yes
      please", and confirm from the transcript that it calls a tool.
- [ ] `uv run python -m pyrrhon.evals.grounding evals/grounding.yaml --repo . --repeat 3 --compare baseline.json`
      — no latency regression. Task 3 gives more turns a tool belt by design, so
      compare `first_speech_ms` on *social* turns specifically, not the aggregate.
