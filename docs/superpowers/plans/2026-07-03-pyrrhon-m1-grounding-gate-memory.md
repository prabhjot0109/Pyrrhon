# Pyrrhon M1 — Trust: Grounding Gate, Grounding Eval, Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Interface drift warning:** written before M0 landed. Before executing, revalidate every Consumes signature against the actual codebase and update this plan if drifted.

**Goal:** Confident hallucination cannot reach the output channels. Every `file:line` in a final answer is mechanically verified (file exists, line exists) before it is emitted; unverifiable references are either fixed by one self-correction retry (screen path) or stripped and replaced with an honest "I couldn't verify that location." A YAML-driven grounding eval scores the agent against a known repo, and a `remember` tool gives the agent append-only session memory in `<repo>/.pyrrhon/memory.md`.

**Architecture:** The gate is a pure `core/grounding/` component (`GroundingGate.check(text) -> GroundedText`) that the agent loop consults after the LLM's final text and before any `SpeechChunk`/`Citation` is yielded — channels never see ungated text. The spec's split-path recovery policy becomes an `Agent` constructor flag: `allow_retry=True` (screen channels, one LLM round-trip to self-correct) vs `allow_retry=False` (the M3 speech path, strip-and-hedge immediately — a retry costs a full LLM turnaround and breaks the voice latency budget). The eval lives in `pyrrhon/evals/` (a sibling of `core/`, not inside it) and drives the same `Agent.run_turn` event stream the channels consume.

**Tech Stack:** Python ≥ 3.12, uv, pydantic v2, openai SDK (as OpenAI-compatible client), rich, **pyyaml (new)**, pytest + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` — the M1 milestone plus the "Grounding gate", "Session memory: `memory.md`", and "Real-time discipline" sections (amendments of 2026-07-03) are binding.

**Prior plan (interfaces consumed here):** `docs/superpowers/plans/2026-07-03-pyrrhon-m0-grounded-text-repl.md` — events, `Tool` ABC, `Agent`, `OpenAICompatLLM`/`LLMReply`, `Settings`, `build_agent`, `FakeLLM`, and the `tests/fixtures/sample_repo` fixture.

## Global Constraints

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.** (`pyrrhon/evals/` is *outside* `core/` and may import `pyrrhon.repl`; `core/` must not import `pyrrhon.evals` either.)
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then. In M1 this applies to the gate's file reads and the memory tool's file writes.
- Grounding verification in M1 is `file:line` only: file exists under the repo root AND the line number is within the file's line count. No content-match of quoted code, no commit/git-level verification (spec amendment 2026-07-03).
- **Split-path retry policy (spec):** `allow_retry=True` (screen) permits exactly ONE self-correction LLM round-trip when the gate finds unverified references; the retry's result is gated again WITHOUT further retry. `allow_retry=False` (the M3 speech path) strips and hedges immediately. Never more than one retry, ever.
- Commit after every task (green tests only).

## File Structure (delta over M0)

```text
pyrrhon/
├── core/
│   ├── agent/
│   │   └── loop.py             # MODIFIED: gate integration + one-retry policy
│   ├── grounding/
│   │   ├── citations.py        # MODIFIED: + extract_references (no existence filter)
│   │   └── gate.py             # NEW: GroundedText, GroundingGate
│   └── tools/
│       └── memory.py           # NEW: RememberTool → <repo>/.pyrrhon/memory.md
├── evals/
│   ├── __init__.py             # NEW (empty)
│   └── grounding.py            # NEW: EvalReport, run_eval, `python -m` CLI
└── repl.py                     # MODIFIED: build_agent wires gate + remember tool

evals/
└── grounding.yaml              # NEW: question → expected-citation cases (v0)

tests/
├── test_citations.py           # MODIFIED: + extract_references tests
├── test_grounding_gate.py      # NEW
├── test_agent_gate.py          # NEW
├── test_init_and_repl.py       # MODIFIED: gate wiring + remember registration
├── test_memory_tool.py         # NEW
└── test_grounding_eval.py      # NEW
```

Later milestones (each gets its own plan): M2 Textual TUI, M3 Pipecat voice (barge-in, `TruncateSpeech` history sync, turn cancellation — the speech channel constructs `Agent` with `allow_retry=False`), M4 tree-sitter/git/web tools, M5 MCP + fallbacks, M6 design mode, M7 plugin loader.

---

### Task 1: `extract_references` — all `path:line` matches, no existence filtering

**Files:**
- Modify: `pyrrhon/core/grounding/citations.py`
- Modify (append tests): `tests/test_citations.py`

**Interfaces:**
- Consumes: `_CITATION_RE` and `extract_citations(text: str, root: Path) -> list[Citation]` from M0 Task 6 (`pyrrhon/core/grounding/citations.py`). `extract_citations` behavior stays **unchanged** — the agent loop still uses it on the gate-less path, and its three existing tests must keep passing.
- Produces: `extract_references(text: str) -> list[tuple[str, int]]` — every `path:line` match in order, backslashes normalized to `/`, **no existence filtering and no dedupe** (the gate needs to see fabricated paths; dedupe is the gate's job).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_citations.py`:

```python
from pyrrhon.core.grounding.citations import extract_references


def test_extract_references_keeps_nonexistent_paths():
    refs = extract_references("see made/up/file.py:12 and app.py:5")
    assert refs == [("made/up/file.py", 12), ("app.py", 5)]


def test_extract_references_normalizes_backslashes_and_keeps_duplicates():
    refs = extract_references(r"utils\helpers.py:1 twice: utils/helpers.py:1")
    assert refs == [("utils/helpers.py", 1), ("utils/helpers.py", 1)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_citations.py -v`
Expected: 3 passed (M0 tests), 2 FAILED with `ImportError: cannot import name 'extract_references'`

- [ ] **Step 3: Write minimal implementation**

Append to `pyrrhon/core/grounding/citations.py` (below `extract_citations`, which is untouched):

```python
def extract_references(text: str) -> list[tuple[str, int]]:
    """Every path:line match in prose — no existence filtering, no dedupe.

    The grounding gate (gate.py) verifies these mechanically; fabricated
    paths must survive extraction so the gate can catch and strip them.
    extract_citations above keeps its M0 existence-filtered behavior.
    """
    return [
        (match.group("path").replace("\\", "/"), int(match.group("line")))
        for match in _CITATION_RE.finditer(text)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_citations.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: 32 passed (M0's 30 + these 2)

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/grounding/citations.py tests/test_citations.py
git commit -m "feat: extract_references — unfiltered path:line extraction for the gate"
```

---

### Task 2: The grounding gate — `GroundedText` + `GroundingGate.check`

**Files:**
- Create: `pyrrhon/core/grounding/gate.py`
- Test: `tests/test_grounding_gate.py`

**Interfaces:**
- Consumes: `extract_references` (Task 1), `Citation` from `pyrrhon.core.events` (M0 Task 4), fixture repo `tests/fixtures/sample_repo` (M0 Task 5: `app.py` has 9 lines, `utils/helpers.py` has 2 lines).
- Produces (in `pyrrhon/core/grounding/gate.py` — **pinned interface, later milestones depend on these exact names**):
  - `@dataclass(frozen=True) GroundedText`: `speech_text: str`, `citations: tuple[Citation, ...]`, `unverified: tuple[str, ...]`
  - `class GroundingGate`: `__init__(self, root: Path)`; `async def check(self, text: str) -> GroundedText`
  - Verification rule: file exists under `root` (and does not escape it) AND `1 <= line <= line count`. All file reads via `asyncio.to_thread` (real-time discipline).
  - `speech_text` = input text with each unverified `path:line` occurrence stripped (both `/` and `\` spellings), runs of spaces collapsed, plus the sentence `I couldn't verify that location.` appended exactly once if anything was stripped.
  - `citations` = verified references as `Citation(file, line)`, deduped preserving order; `unverified` = failed references as `"path:line"` strings, deduped preserving order.

- [ ] **Step 1: Write the failing test**

`tests/test_grounding_gate.py`:

```python
from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.gate import GroundedText, GroundingGate

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_verified_citation_passes_through_untouched():
    gate = GroundingGate(FIXTURE)
    out = await gate.check("greet lives at utils/helpers.py:1.")
    assert isinstance(out, GroundedText)
    assert out.speech_text == "greet lives at utils/helpers.py:1."
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ()


async def test_missing_file_is_stripped_and_hedged():
    gate = GroundingGate(FIXTURE)
    out = await gate.check("see made/up/file.py:12 for details.")
    assert "made/up/file.py" not in out.speech_text
    assert out.speech_text == "see for details. I couldn't verify that location."
    assert out.citations == ()
    assert out.unverified == ("made/up/file.py:12",)


async def test_line_past_end_of_file_fails_verification():
    # utils/helpers.py has only 2 lines — the file is real, the line is not.
    out = await GroundingGate(FIXTURE).check("see utils/helpers.py:999.")
    assert out.citations == ()
    assert out.unverified == ("utils/helpers.py:999",)
    assert out.speech_text.endswith("I couldn't verify that location.")


async def test_mixed_refs_keep_verified_and_hedge_once():
    text = "greet is at utils/helpers.py:1; also bogus.py:3 and fake/x.py:9."
    out = await GroundingGate(FIXTURE).check(text)
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ("bogus.py:3", "fake/x.py:9")
    assert "utils/helpers.py:1" in out.speech_text
    assert out.speech_text.count("I couldn't verify that location.") == 1


async def test_backslash_reference_verifies_as_posix():
    out = await GroundingGate(FIXTURE).check(r"look at utils\helpers.py:1")
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ()


async def test_unverified_backslash_form_is_stripped_from_speech():
    out = await GroundingGate(FIXTURE).check(r"see fake\thing.py:2 here")
    assert out.unverified == ("fake/thing.py:2",)
    assert out.speech_text == "see here I couldn't verify that location."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounding_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.grounding.gate'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/grounding/gate.py`:

```python
"""The grounding gate: mechanical verification of file:line claims.

Runs between the LLM's final text and the output channels — nothing reaches
the speakers (or the screen) carrying a reference this gate could not verify.
Verification is file:line only: the file exists inside the repo and the line
number is within its line count (spec "Grounding gate", amended 2026-07-03).
Unverifiable references are stripped from the speakable text and replaced
with a single honest hedge sentence.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_references

HEDGE = "I couldn't verify that location."


@dataclass(frozen=True)
class GroundedText:
    speech_text: str
    citations: tuple[Citation, ...]
    unverified: tuple[str, ...]


class GroundingGate:
    def __init__(self, root: Path):
        self.root = root

    async def check(self, text: str) -> GroundedText:
        # Real-time discipline: every file read happens off the event loop.
        return await asyncio.to_thread(self._check_sync, text)

    def _check_sync(self, text: str) -> GroundedText:
        line_counts: dict[str, int | None] = {}
        verified: list[Citation] = []
        unverified: list[str] = []
        seen_ok: set[tuple[str, int]] = set()
        seen_bad: set[str] = set()

        for rel, line in extract_references(text):
            if rel not in line_counts:
                line_counts[rel] = self._count_lines(rel)
            count = line_counts[rel]
            if count is not None and 1 <= line <= count:
                if (rel, line) not in seen_ok:
                    seen_ok.add((rel, line))
                    verified.append(Citation(file=rel, line=line))
            else:
                ref = f"{rel}:{line}"
                if ref not in seen_bad:
                    seen_bad.add(ref)
                    unverified.append(ref)

        speech = text
        if unverified:
            for ref in unverified:
                rel, _, line_str = ref.rpartition(":")
                # Match both the normalized (/) and original (\) spellings;
                # \b after the line number keeps app.py:5 from eating app.py:55.
                pattern = re.compile(
                    re.escape(rel).replace("/", r"[/\\]") + ":" + line_str + r"\b"
                )
                speech = pattern.sub("", speech)
            speech = re.sub(r"[ \t]{2,}", " ", speech).strip()
            speech = f"{speech} {HEDGE}" if speech else HEDGE

        return GroundedText(
            speech_text=speech,
            citations=tuple(verified),
            unverified=tuple(unverified),
        )

    def _count_lines(self, rel: str) -> int | None:
        """Line count of a repo file, or None if missing/unreadable/escaping."""
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            return None  # ../-style escape — never verify outside the repo
        if not target.is_file():
            return None
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return len(content.splitlines())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounding_gate.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: 38 passed

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/grounding/gate.py tests/test_grounding_gate.py
git commit -m "feat: grounding gate — verify file:line claims, strip and hedge failures"
```

---

### Task 3: Agent integration — gate the final reply, one retry on the screen path

**Files:**
- Modify: `pyrrhon/core/agent/loop.py`
- Modify: `pyrrhon/repl.py` (`build_agent` wires the gate)
- Modify (append test): `tests/test_init_and_repl.py`
- Test: `tests/test_agent_gate.py`

**Interfaces:**
- Consumes: `Agent` (M0 Task 8), `GroundingGate`/`GroundedText` (Task 2), `SpeechChunk`/`Citation` events (M0 Task 4), `LLMReply` (M0 Task 3), `extract_citations` (M0 Task 6, kept for the gate-less path), `FakeLLM` (M0 Task 4, tests only), `build_agent` (M0 Task 9).
- Produces (**pinned interface**):
  - `Agent.__init__` gains keyword args `grounding_gate: GroundingGate | None = None, allow_retry: bool = True` (all other parameters unchanged).
  - Gated final-reply contract: when a gate is set, the final text is checked; if `gated.unverified` is non-empty and `allow_retry` is True, the agent does exactly ONE retry — it sends `history + [assistant draft, user-role correction listing the failed citations and asking the model to fix or hedge them]` to the LLM (a single `chat` call, `tools=None` — never a new tool loop), then gates that result WITHOUT further retry. The draft and correction never enter the caller's `history`; only the final `gated.speech_text` is appended as the assistant message (history records what the user was shown).
  - Emission: `SpeechChunk(gated.speech_text)` then one event per `gated.citations` entry — the raw `extract_citations` output is used only when `grounding_gate is None` (backward-compatible M0 behavior).
  - `build_agent(repo_root, llm=None)` now constructs the Agent with `grounding_gate=GroundingGate(repo_root)` (and the default `allow_retry=True` — the REPL is a screen channel; M3's speech path will pass `allow_retry=False`).

- [ ] **Step 1: Write the failing test**

`tests/test_agent_gate.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation, SpeechChunk
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_agent(replies, *, allow_retry: bool = True) -> tuple[Agent, FakeLLM]:
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
        grounding_gate=GroundingGate(FIXTURE),
        allow_retry=allow_retry,
    )
    return agent, fake


async def collect(agent: Agent, history: list[dict], text: str) -> list:
    return [event async for event in agent.run_turn(history, text)]


async def test_verified_reply_passes_gate_without_retry():
    agent, fake = make_agent([LLMReply(text="greet is at utils/helpers.py:1.")])
    events = await collect(agent, [], "where is greet?")
    assert SpeechChunk(text="greet is at utils/helpers.py:1.") in events
    assert Citation(file="utils/helpers.py", line=1) in events
    assert len(fake.calls) == 1  # verified — no retry round-trip


async def test_unverified_reply_triggers_exactly_one_retry():
    agent, fake = make_agent(
        [
            LLMReply(text="greet is at bogus/nowhere.py:7."),
            LLMReply(text="Correction: greet is at utils/helpers.py:1."),
        ]
    )
    history: list[dict] = []
    events = await collect(agent, history, "where is greet?")

    assert len(fake.calls) == 2
    retry_messages = fake.calls[1]["messages"]
    # The retry sees its own draft, then a user-role correction naming the failure:
    assert retry_messages[-2] == {
        "role": "assistant",
        "content": "greet is at bogus/nowhere.py:7.",
    }
    assert retry_messages[-1]["role"] == "user"
    assert "bogus/nowhere.py:7" in retry_messages[-1]["content"]
    assert fake.calls[1]["tools"] is None  # single round-trip, no new tool loop

    assert SpeechChunk(text="Correction: greet is at utils/helpers.py:1.") in events
    assert Citation(file="utils/helpers.py", line=1) in events
    # The draft and correction never entered the caller's history:
    assert [m["role"] for m in history] == ["system", "user", "assistant"]
    assert history[-1] == {
        "role": "assistant",
        "content": "Correction: greet is at utils/helpers.py:1.",
    }


async def test_retry_result_is_gated_without_second_retry():
    agent, fake = make_agent(
        [
            LLMReply(text="see bogus.py:3."),
            LLMReply(text="still bogus: other/fake.py:9."),
        ]
    )
    events = await collect(agent, [], "where?")
    assert len(fake.calls) == 2  # exactly one retry, never two
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert "other/fake.py:9" not in speech[-1].text
    assert speech[-1].text.endswith("I couldn't verify that location.")
    assert not any(isinstance(e, Citation) for e in events)


async def test_allow_retry_false_strips_immediately():
    agent, fake = make_agent(
        [LLMReply(text="see bogus.py:3 for details.")], allow_retry=False
    )
    events = await collect(agent, [], "where?")
    assert len(fake.calls) == 1  # speech path: no retry round-trip, ever
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "see for details. I couldn't verify that location."


async def test_no_gate_keeps_m0_behavior():
    fake = FakeLLM([LLMReply(text="see bogus.py:3.")])
    agent = Agent(llm=fake, tools=[], system_prompt="t", repo_root=FIXTURE)
    events = [event async for event in agent.run_turn([], "hi")]
    assert events == [SpeechChunk(text="see bogus.py:3.")]  # ungated, uncited
```

Append to `tests/test_init_and_repl.py` (new import at the top of the file: `from pyrrhon.core.grounding.gate import GroundingGate`):

```python
def test_build_agent_wires_grounding_gate():
    fake = FakeLLM([])
    agent = build_agent(FIXTURE, llm=fake)
    assert isinstance(agent.grounding_gate, GroundingGate)
    assert agent.grounding_gate.root == FIXTURE
    assert agent.allow_retry is True  # REPL is a screen channel
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_gate.py tests/test_init_and_repl.py -v`
Expected: FAIL — `TypeError: Agent.__init__() got an unexpected keyword argument 'grounding_gate'` (and the `test_build_agent_wires_grounding_gate` failure)

- [ ] **Step 3: Write minimal implementation**

Replace `pyrrhon/core/agent/loop.py` in full with:

```python
"""The reasoning loop: LLM ⇄ tools, emitting the core event stream.

M1: a GroundingGate can sit between the LLM's final text and the emitted
events. Split-path recovery policy (spec, amended 2026-07-03): screen
channels construct the Agent with allow_retry=True and get one
self-correction LLM round-trip; the M3 speech channel constructs it with
allow_retry=False and unverifiable references are stripped immediately —
a retry costs a full LLM turnaround and breaks the voice latency budget.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from pyrrhon.core.events import (
    Event,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
)
from pyrrhon.core.grounding.citations import extract_citations
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.base import Tool

PREVIEW_LEN = 200


def _retry_prompt(unverified: tuple[str, ...]) -> str:
    refs = ", ".join(unverified)
    return (
        "Grounding check failed: these citations do not point at real "
        f"locations in the repo: {refs}. Rewrite your answer using only "
        "path:line locations you actually saw in tool output earlier in this "
        "conversation. If you are not sure of the exact location, say "
        "\"I'm not certain\" and drop the citation. Never invent a path."
    )


class Agent:
    """Owns no conversation state: `history` belongs to the caller and is
    mutated in place, so channels (REPL/TUI/voice) decide session lifetime."""

    def __init__(
        self,
        llm,
        tools: list[Tool],
        system_prompt: str,
        repo_root: Path,
        max_tool_rounds: int = 8,
        grounding_gate: GroundingGate | None = None,
        allow_retry: bool = True,
    ):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        self.system_prompt = system_prompt
        self.repo_root = repo_root
        self.max_tool_rounds = max_tool_rounds
        self.grounding_gate = grounding_gate
        self.allow_retry = allow_retry

    async def run_turn(
        self, history: list[dict], user_text: str
    ) -> AsyncIterator[Event]:
        if not history:
            history.append({"role": "system", "content": self.system_prompt})
        history.append({"role": "user", "content": user_text})
        schemas = [tool.schema() for tool in self.tools.values()]

        for _ in range(self.max_tool_rounds):
            reply = await self.llm.chat(history, tools=schemas)
            if not reply.tool_calls:
                text = reply.text or "(no answer)"

                if self.grounding_gate is None:
                    # Backward-compatible M0 path: no verification.
                    history.append({"role": "assistant", "content": text})
                    yield SpeechChunk(text=text)
                    for citation in extract_citations(text, self.repo_root):
                        yield citation
                    return

                gated = await self.grounding_gate.check(text)
                if gated.unverified and self.allow_retry:
                    # Exactly ONE self-correction round-trip (screen path).
                    # The draft and the correction never enter `history` —
                    # history records what the user was shown, not drafts.
                    retry_messages = [
                        *history,
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": _retry_prompt(gated.unverified)},
                    ]
                    # tools=None: the retry is a single LLM call, never a new
                    # tool loop — the model fixes from context or hedges.
                    retry_reply = await self.llm.chat(retry_messages, tools=None)
                    text = retry_reply.text or text
                    # Gate the retry result WITHOUT further retry.
                    gated = await self.grounding_gate.check(text)

                history.append({"role": "assistant", "content": gated.speech_text})
                yield SpeechChunk(text=gated.speech_text)
                for citation in gated.citations:
                    yield citation
                return

            history.append(_assistant_tool_message(reply))
            for call in reply.tool_calls:
                yield ToolCallStarted(name=call.name, args=call.arguments)
                result = await self._run_tool(call.name, call.arguments)
                history.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
                yield ToolCallFinished(name=call.name, result_preview=result[:PREVIEW_LEN])

        text = (
            "I hit my tool budget for this question — ask me to continue "
            "and I'll keep digging."
        )
        history.append({"role": "assistant", "content": text})
        yield SpeechChunk(text=text)

    async def _run_tool(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"ERROR: no tool named '{name}'."
        try:
            return await tool.run(**args)
        except TypeError as exc:
            return f"ERROR: bad arguments for {name}: {exc}"


def _assistant_tool_message(reply: LLMReply) -> dict:
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

In `pyrrhon/repl.py`, add the import

```python
from pyrrhon.core.grounding.gate import GroundingGate
```

and replace `build_agent` in full with:

```python
def build_agent(repo_root: Path, llm=None) -> Agent:
    settings = load_settings(repo_root)
    llm = llm or create_llm(settings.fast, settings)
    tools = [ReadFileTool(repo_root), GrepTool(repo_root), GlobTool(repo_root)]
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=build_system_prompt(repo_root),
        repo_root=repo_root,
        grounding_gate=GroundingGate(repo_root),
        # REPL is a screen channel → default allow_retry=True. M3's speech
        # path constructs its Agent with allow_retry=False (spec split-path).
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_gate.py tests/test_init_and_repl.py -v`
Expected: 8 passed (5 new gate-integration + 1 new wiring + 2 existing)

- [ ] **Step 5: Run the whole suite (guard against regressions — M0's agent-loop tests must still pass ungated)**

Run: `uv run pytest -q`
Expected: 44 passed

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent/loop.py pyrrhon/repl.py tests/test_agent_gate.py tests/test_init_and_repl.py
git commit -m "feat: agent gates final replies — one screen-path retry, strip-and-hedge"
```

---

### Task 4: `remember` tool — append-only session memory in `.pyrrhon/memory.md`

**Files:**
- Create: `pyrrhon/core/tools/memory.py`
- Modify: `pyrrhon/repl.py` (register in `build_agent`)
- Modify: `tests/test_init_and_repl.py` (tool-roster assertion)
- Test: `tests/test_memory_tool.py`

**Interfaces:**
- Consumes: `Tool` ABC (M0 Task 5), `build_agent` (Task 3's version).
- Produces (**pinned interface**): `class RememberTool(Tool)` in `pyrrhon/core/tools/memory.py` — `name = "remember"`, `parameters` requiring `{fact: str}`; `__init__(self, root: Path)`; appends `- [YYYY-MM-DD] {fact}` (real current date via `datetime.date.today()`) to `<root>/.pyrrhon/memory.md`, creating the directory and a `# Memory\n` header if missing. Never overwrites existing content (append-only; the user may edit or prune the file freely — spec "Session memory"). File writes via `asyncio.to_thread`. Registered in `build_agent` alongside the other three tools. Reading needs no tool: `.pyrrhon/*.md` is already ingested by the M0 soul loader at session start.

- [ ] **Step 1: Write the failing test**

`tests/test_memory_tool.py`:

```python
from datetime import date
from pathlib import Path

from pyrrhon.core.tools.memory import RememberTool


async def test_remember_creates_file_with_header_and_dated_bullet(tmp_path: Path):
    tool = RememberTool(tmp_path)
    out = await tool.run(fact="The user prefers first-principles answers.")
    assert out.startswith("Remembered:")
    memory = tmp_path / ".pyrrhon" / "memory.md"
    content = memory.read_text(encoding="utf-8")
    assert content.startswith("# Memory\n")
    today = date.today().isoformat()
    assert f"- [{today}] The user prefers first-principles answers.\n" in content


async def test_remember_appends_in_order_without_clobbering(tmp_path: Path):
    tool = RememberTool(tmp_path)
    await tool.run(fact="first fact")
    await tool.run(fact="second fact")
    lines = (
        (tmp_path / ".pyrrhon" / "memory.md")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert lines[0] == "# Memory"
    assert lines[1].endswith("first fact")
    assert lines[2].endswith("second fact")


async def test_remember_flattens_newlines_to_keep_one_bullet_per_fact(tmp_path: Path):
    await RememberTool(tmp_path).run(fact="line one\nline two")
    content = (tmp_path / ".pyrrhon" / "memory.md").read_text(encoding="utf-8")
    assert "line one line two" in content
    assert "line one\nline two" not in content


async def test_remember_preserves_user_edited_memory(tmp_path: Path):
    directory = tmp_path / ".pyrrhon"
    directory.mkdir()
    (directory / "memory.md").write_text(
        "# Memory\n- [2026-01-01] old fact\n", encoding="utf-8"
    )
    await RememberTool(tmp_path).run(fact="new fact")
    content = (directory / "memory.md").read_text(encoding="utf-8")
    assert "old fact" in content
    assert "new fact" in content


async def test_remember_schema_shape():
    schema = RememberTool(Path(".")).schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "remember"
    assert schema["function"]["parameters"]["required"] == ["fact"]
    assert "fact" in schema["function"]["parameters"]["properties"]
```

In `tests/test_init_and_repl.py`, update the roster assertion inside `test_build_agent_wires_tools_and_answers` — replace:

```python
    assert set(agent.tools) == {"read_file", "grep", "glob"}
```

with:

```python
    assert set(agent.tools) == {"read_file", "grep", "glob", "remember"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_memory_tool.py tests/test_init_and_repl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyrrhon.core.tools.memory'`, plus the roster assertion failing (no `remember` yet)

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/tools/memory.py`:

```python
"""The remember tool: append-only session memory in <repo>/.pyrrhon/memory.md.

The agent calls it when something is worth carrying across sessions —
decisions made, corrections the user gave, repo quirks discovered. Reading
is free: memory.md sits in .pyrrhon/, so the soul loader already ingests it
at session start. The user may edit or prune the file freely; this tool only
ever appends (spec "Session memory: memory.md", added 2026-07-03).

Real-time discipline: the file write is offloaded via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

from pyrrhon.core.tools.base import Tool

MEMORY_HEADER = "# Memory\n"


class RememberTool(Tool):
    name = "remember"
    description = (
        "Save a key fact worth keeping across sessions (a decision, a user "
        "correction, a repo quirk). Appends a dated bullet to .pyrrhon/memory.md."
    )
    parameters = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "One self-contained sentence to remember",
            },
        },
        "required": ["fact"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, fact: str) -> str:
        return await asyncio.to_thread(self._append, fact)

    def _append(self, fact: str) -> str:
        fact = " ".join(fact.split())  # one bullet per fact — no embedded newlines
        if not fact:
            return "ERROR: nothing to remember (empty fact)."
        directory = self.root / ".pyrrhon"
        try:
            directory.mkdir(exist_ok=True)
            memory = directory / "memory.md"
            if not memory.exists():
                memory.write_text(MEMORY_HEADER, encoding="utf-8")
            stamp = datetime.date.today().isoformat()
            with memory.open("a", encoding="utf-8") as f:
                f.write(f"- [{stamp}] {fact}\n")
        except OSError as exc:
            return f"ERROR: could not write memory.md: {exc}"
        return f"Remembered: {fact}"
```

In `pyrrhon/repl.py`, add the import

```python
from pyrrhon.core.tools.memory import RememberTool
```

and replace `build_agent` in full with:

```python
def build_agent(repo_root: Path, llm=None) -> Agent:
    settings = load_settings(repo_root)
    llm = llm or create_llm(settings.fast, settings)
    tools = [
        ReadFileTool(repo_root),
        GrepTool(repo_root),
        GlobTool(repo_root),
        RememberTool(repo_root),
    ]
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=build_system_prompt(repo_root),
        repo_root=repo_root,
        grounding_gate=GroundingGate(repo_root),
        # REPL is a screen channel → default allow_retry=True. M3's speech
        # path constructs its Agent with allow_retry=False (spec split-path).
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_memory_tool.py tests/test_init_and_repl.py -v`
Expected: 8 passed (5 new + 3 in test_init_and_repl.py)

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: 49 passed

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/tools/memory.py pyrrhon/repl.py tests/test_memory_tool.py tests/test_init_and_repl.py
git commit -m "feat: remember tool — dated append-only bullets in .pyrrhon/memory.md"
```

---

### Task 5: Grounding eval v0 — YAML cases, `run_eval`, CLI

**Files:**
- Create: `pyrrhon/evals/__init__.py` (empty), `pyrrhon/evals/grounding.py`, `evals/grounding.yaml`
- Modify: `pyproject.toml` (via `uv add pyyaml`), `CLAUDE.md` (record the eval command)
- Test: `tests/test_grounding_eval.py`

**Interfaces:**
- Consumes: `Agent` (Task 3's version), `GroundingGate` (Task 2), `Citation` (M0 Task 4), `LLMReply`/`FakeLLM` (M0 Tasks 3–4, tests only), `build_agent` (Task 4's version, CLI only), fixture repo `tests/fixtures/sample_repo`.
- Produces (**pinned interface**), in `pyrrhon/evals/grounding.py`:
  - `@dataclass EvalReport`: `total: int`, `passed: int`, `failures: list[str]`
  - `run_eval(yaml_path: Path, agent_factory) -> EvalReport` — `agent_factory` is a zero-arg callable returning a fresh `Agent` per case; a case passes if **any** emitted `Citation` matches an expected entry with the file equal exactly and the line within ±5. `run_eval` is a sync function (it calls `asyncio.run` internally), so its unit tests are sync `def`s.
  - A `__main__`-style CLI: `uv run python -m pyrrhon.evals.grounding evals/grounding.yaml` (builds a real agent via `build_agent`; exit code 0 iff all cases pass).
  - `evals/grounding.yaml`: a list of `{question, expected: [{file, line}]}` cases against `tests/fixtures/sample_repo`.
- New dependency: `pyyaml` (runtime, for the eval loader).

- [ ] **Step 1: Add the dependency**

Run:

```bash
uv add pyyaml
```

Then: `uv sync` — Expected: resolves and installs without error (`pyyaml` appears in `[project.dependencies]` in `pyproject.toml`).

- [ ] **Step 2: Create the eval case file**

`evals/grounding.yaml`:

```yaml
# Grounding eval v0 — question → expected-citation pairs against
# tests/fixtures/sample_repo. A case passes if any emitted Citation matches
# the expected file exactly with the line within ±5. Run with a real LLM:
#   uv run python -m pyrrhon.evals.grounding evals/grounding.yaml
- question: "Where is the greet function defined?"
  expected:
    - {file: utils/helpers.py, line: 1}
- question: "Where does the app call greet?"
  expected:
    - {file: app.py, line: 5}
- question: "Where is the main entry function of the sample app defined?"
  expected:
    - {file: app.py, line: 4}
```

- [ ] **Step 3: Write the failing test**

`tests/test_grounding_eval.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.evals.grounding import EvalReport, run_eval
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

TWO_CASES = """\
- question: "Where is the greet function defined?"
  expected:
    - {file: utils/helpers.py, line: 1}
- question: "Where is greet called from?"
  expected:
    - {file: app.py, line: 5}
"""

ONE_CASE = """\
- question: "Where is greet?"
  expected:
    - {file: utils/helpers.py, line: 1}
"""


def make_factory(scripts: list[list[LLMReply]]):
    """One scripted reply-list per eval case, consumed in order."""
    queue = [list(replies) for replies in scripts]

    def factory() -> Agent:
        return Agent(
            llm=FakeLLM(queue.pop(0)),
            tools=[],
            system_prompt="You are a test agent.",
            repo_root=FIXTURE,
            grounding_gate=GroundingGate(FIXTURE),
            allow_retry=False,
        )

    return factory


# NOTE: run_eval calls asyncio.run() internally, so these tests are sync defs
# (an async test would already be inside a running loop and asyncio.run fails).


def test_all_cases_pass(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(TWO_CASES, encoding="utf-8")
    factory = make_factory(
        [
            [LLMReply(text="greet is defined at utils/helpers.py:1.")],
            [LLMReply(text="It is called from app.py:5.")],
        ]
    )
    report = run_eval(yaml_path, factory)
    assert report == EvalReport(total=2, passed=2, failures=[])


def test_line_within_tolerance_of_five_passes(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(
        '- question: "Where is main?"\n  expected:\n    - {file: app.py, line: 4}\n',
        encoding="utf-8",
    )
    # app.py:8 is a real line; |8 - 4| = 4 <= 5 → pass.
    factory = make_factory([[LLMReply(text="main runs from app.py:8.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 1
    assert report.failures == []


def test_failure_lists_question_expected_and_got(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    factory = make_factory([[LLMReply(text="I could not find it.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 0
    assert len(report.failures) == 1
    assert "Where is greet?" in report.failures[0]
    assert "utils/helpers.py:1" in report.failures[0]
    assert "no citations" in report.failures[0]


def test_wrong_file_fails_even_if_line_is_close(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    # app.py:1 is a real, verifiable citation — but the file must match exactly.
    factory = make_factory([[LLMReply(text="greet is at app.py:1.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 0
    assert "app.py:1" in report.failures[0]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_grounding_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.evals'`

- [ ] **Step 5: Write minimal implementation**

Create empty `pyrrhon/evals/__init__.py`.

`pyrrhon/evals/grounding.py`:

```python
"""Grounding eval v0: score the agent's citations against expected file:line.

This is the metric for VISION.md's open question "how do we measure
cited-the-right-file:line". Cases are YAML: a list of
{question, expected: [{file, line}]}. A case passes if any Citation the
agent emits matches an expected entry — file equal exactly, line within ±5.

Run against the checked-in case set (real LLM, needs an API key):

    uv run python -m pyrrhon.evals.grounding evals/grounding.yaml
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import yaml

from pyrrhon.core.events import Citation

LINE_TOLERANCE = 5


@dataclass
class EvalReport:
    total: int
    passed: int
    failures: list[str]


def run_eval(yaml_path: Path, agent_factory) -> EvalReport:
    """Run every case with a fresh agent from `agent_factory()`.

    Sync on purpose: it owns its own event loop via asyncio.run, so the CLI
    (and any script) can call it directly.
    """
    cases = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or []
    return asyncio.run(_run_cases(cases, agent_factory))


async def _run_cases(cases: list[dict], agent_factory) -> EvalReport:
    passed = 0
    failures: list[str] = []
    for case in cases:
        agent = agent_factory()
        citations = [
            event
            async for event in agent.run_turn([], case["question"])
            if isinstance(event, Citation)
        ]
        if _matches(citations, case["expected"]):
            passed += 1
        else:
            got = ", ".join(f"{c.file}:{c.line}" for c in citations) or "no citations"
            want = ", ".join(f"{e['file']}:{e['line']}" for e in case["expected"])
            failures.append(f"{case['question']!r}: expected {want}, got {got}")
    return EvalReport(total=len(cases), passed=passed, failures=failures)


def _matches(citations: list[Citation], expected: list[dict]) -> bool:
    for exp in expected:
        for citation in citations:
            if (
                citation.file == exp["file"]
                and citation.line is not None
                and abs(citation.line - exp["line"]) <= LINE_TOLERANCE
            ):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pyrrhon.evals.grounding",
        description="Score the agent's file:line citations against expected answers.",
    )
    parser.add_argument("yaml_path", type=Path, help="Eval case file (YAML)")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("tests/fixtures/sample_repo"),
        help="Repo the questions are about (default: the test fixture repo)",
    )
    args = parser.parse_args(argv)

    # Imported here, not at module top: only the CLI needs a real,
    # API-key-backed agent — unit tests inject FakeLLM-backed factories.
    from pyrrhon.repl import build_agent

    repo_root = args.repo.resolve()
    report = run_eval(args.yaml_path, lambda: build_agent(repo_root))
    print(f"grounding eval: {report.passed}/{report.total} passed")
    for failure in report.failures:
        print(f"  FAIL {failure}")
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_grounding_eval.py -v`
Expected: 4 passed

- [ ] **Step 7: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: 53 passed

- [ ] **Step 8: Manual smoke test (needs GROQ_API_KEY set)**

Run: `uv run python -m pyrrhon.evals.grounding evals/grounding.yaml` — confirm it prints `grounding eval: N/3 passed` (with any misses listed as `FAIL` lines naming the question, expected, and got) and exits 0 when 3/3. If you have no key handy, confirm instead that it exits with the `Set GROQ_API_KEY...` message from `create_llm`.

- [ ] **Step 9: Record real commands in CLAUDE.md**

In `CLAUDE.md`, replace:

```markdown
There is no lint config yet. Current state: M0 (grounded text REPL) — see
`docs/superpowers/plans/2026-07-03-pyrrhon-m0-grounded-text-repl.md`.
```

with:

```markdown
- Run the grounding eval (real LLM, needs an API key):
  `uv run python -m pyrrhon.evals.grounding evals/grounding.yaml`

There is no lint config yet. Current state: M1 (trust: grounding gate +
grounding eval + memory) — see
`docs/superpowers/plans/2026-07-03-pyrrhon-m1-grounding-gate-memory.md`.
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock pyrrhon/evals evals tests/test_grounding_eval.py CLAUDE.md
git commit -m "feat: grounding eval v0 — YAML cases, run_eval scorer, CLI"
```

---

## Definition of Done (M1)

- `uv run pytest` fully green (53 tests: M0's 30 + 23 from this plan).
- **A fabricated `path:line` cannot reach the output.** Every final reply passes through `GroundingGate`; unverifiable references are either fixed by exactly one self-correction retry (screen path, `allow_retry=True`) or stripped from `speech_text` with a single honest "I couldn't verify that location." appended. Emitted `Citation` events come from the gate's verified set only.
- The split-path policy is a constructor flag, ready for M3: `Agent(..., allow_retry=False)` produces zero retry round-trips (verified by `tests/test_agent_gate.py::test_allow_retry_false_strips_immediately`).
- `remember` is on the tool roster; calling it appends `- [YYYY-MM-DD] fact` bullets to `<repo>/.pyrrhon/memory.md` (created with a `# Memory` header on first use, never clobbering user edits), and the M0 soul loader ingests that file at the next session start — memory persists across sessions with no new read path.
- `uv run python -m pyrrhon.evals.grounding evals/grounding.yaml` scores the agent against the fixture repo and reports `passed/total` with named failures; `run_eval` is exercised hermetically in tests via FakeLLM-backed agent factories (no API calls in the suite).
- `core/` import seam still clean (verify: `grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice\|pyrrhon.evals" pyrrhon/core/` returns nothing).

M2's plan (Textual TUI: transcript pane, code viewer jumping to gated citations, slash commands) follows once M1 lands.
