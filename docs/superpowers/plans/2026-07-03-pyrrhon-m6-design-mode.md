# Pyrrhon M6 — Design Mode (Act 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Interface drift warning:** written before M0–M5 landed. Before executing, revalidate every Consumes signature against the actual codebase and update this plan if drifted.

**Goal:** Act 2. `/mode design` turns Pyrrhon into a skeptical senior architect: it never accepts a proposal immediately, challenges the weakest assumption with a concrete alternative, asks one question per turn (surfaced as `AskUser` events so channels can render/say them distinctly), and only once the reasoning is explicit writes spec artifacts (`PRD.md`, `HLD.md`, `LLD.md`, `api.md`, `database.md`, `risks.md`) to `<repo>/docs/design/` via a new `write_spec` tool — specs that record the *reasoning*, not just the decisions.

**Architecture:** Design mode is a prompt layer plus one tool, not a second agent. `Session.set_mode()` injects a system-role message on top of the base teaching prompt (which stays from turn one); `WriteSpecTool` is always registered in `build_agent` — the `DESIGN_PROMPT` is what instructs its use, and the understand-mode prompt explicitly forbids it. The `AskUser` event defined in M0 finally gets a producer: a pure `extract_question()` in the agent loop. `core/` still imports nothing from `repl.py`/`tui/`/`voice/`/`commands/`.

**Tech Stack:** Python ≥ 3.12, uv, pydantic v2, openai SDK (as OpenAI-compatible client), rich, pytest + pytest-asyncio + respx. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` (M6 milestone; "Teaching policy" design-mode bullet; `AskUser` event). Product behavior: `VISION.md` "Act 2 — Design" and success criterion 4.

## Global Constraints

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.**
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then.
- No grounding *verification* in M0 (that is M1); M0 only extracts citations for files that exist.
- Commit after every task (green tests only).

M6 notes on the constraints above: the "no grounding verification" bullet is copied verbatim from M0 and is superseded — the gate landed in M1 and stays on in design mode (design prose rarely contains `file:line` claims, so it passes through untouched). One M6 addition: **`write_spec` is the only tool that writes inside the repo, it may write only under `docs/design/`, and only the six allowlisted filenames** — the allowlist is enforced in code, not just in the prompt.

## Assumed interfaces from M1–M5 (revalidate per the drift warning)

- `pyrrhon/core/session.py`: `class Session` constructed as `Session(agent)`, holding `history: list[dict]` and `mode: str = "understand"`.
- `pyrrhon/commands/registry.py`: decorator `command(name, help_text)`, dispatcher `dispatch(line, ctx)`, and `CommandContext(repo_root, agent, ui)` (a dataclass; M6 adds a `session` field in Task 4).
- `pyrrhon/core/agent/loop.py`: `Agent` as in M0 plus kwargs `grounding_gate`, `allow_retry`, `deep_llm` added by M1/M4 — Task 3 appends a `mode` kwarg after whatever the current signature is.
- `pyrrhon/repl.py`: `build_agent(repo_root, llm=None) -> Agent` still injectable for tests.
- `pyrrhon/config/settings.py`: `Settings.deep_slot` exists; M4's escalation already routes design interrogation and spec writing to the deep model, so M6 adds no model-slot code.

## File Structure (delta for this plan)

```text
pyrrhon/
├── repl.py                       # MODIFIED: build_agent registers write_spec
├── commands/
│   ├── registry.py               # MODIFIED: CommandContext gains `session`
│   └── mode_cmd.py               # NEW: /mode understand|design
└── core/
    ├── session.py                # MODIFIED: set_mode() + mode message injection
    ├── agent/
    │   ├── design_prompts.py     # NEW: DESIGN_PROMPT (the skeptic policy)
    │   ├── prompts.py            # MODIFIED: understand mode forbids write_spec
    │   └── loop.py               # MODIFIED: Agent.mode, extract_question, AskUser
    └── tools/
        └── spec_writer.py        # NEW: WriteSpecTool (write_spec)

tests/
├── test_spec_writer.py           # NEW
├── test_design_prompts.py        # NEW
├── test_extract_question.py      # NEW
├── test_session_mode.py          # NEW
├── test_mode_command.py          # NEW
└── test_design_session_e2e.py    # NEW
```

---

### Task 1: WriteSpecTool — the write_spec tool with a filename allowlist

**Files:**
- Create: `pyrrhon/core/tools/spec_writer.py`
- Test: `tests/test_spec_writer.py`

**Interfaces:**
- Consumes: `Tool` ABC from `pyrrhon/core/tools/base.py` (M0 Task 5: class attrs `name`, `description`, `parameters`; `async run(**kwargs) -> str`; failures are `"ERROR: ..."` strings).
- Produces:
  - `SPEC_FILENAMES: tuple[str, ...]` — exactly `("PRD.md", "HLD.md", "LLD.md", "api.md", "database.md", "risks.md")`; the single source of truth, imported by `design_prompts.py` in Task 2.
  - `class WriteSpecTool(Tool)`: `name = "write_spec"`; parameters require `{filename: enum of SPEC_FILENAMES, content: str}`; `__init__(root: Path)`; writes `<root>/docs/design/<filename>` (creating the directory), refuses any other filename with an `ERROR:` string, allows overwrite (the conversation is the source of truth), file I/O via `asyncio.to_thread`. Emits no events itself — its return string goes back to the LLM, and the agent's final `SpeechChunk` announces what was written.

- [ ] **Step 1: Write the failing test**

`tests/test_spec_writer.py`:

```python
from pathlib import Path

from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES, WriteSpecTool


async def test_writes_allowed_filename_creating_dir(tmp_path: Path):
    tool = WriteSpecTool(tmp_path)
    out = await tool.run(filename="PRD.md", content="# PRD\n\nWhy: because reasons.\n")
    assert out.startswith("Wrote docs/design/PRD.md")
    written = tmp_path / "docs" / "design" / "PRD.md"
    assert written.read_text(encoding="utf-8") == "# PRD\n\nWhy: because reasons.\n"


async def test_overwrite_is_allowed_and_reported(tmp_path: Path):
    tool = WriteSpecTool(tmp_path)
    await tool.run(filename="risks.md", content="v1")
    out = await tool.run(filename="risks.md", content="v2 — conversation moved on")
    assert out.startswith("Overwrote docs/design/risks.md")
    written = tmp_path / "docs" / "design" / "risks.md"
    assert written.read_text(encoding="utf-8") == "v2 — conversation moved on"


async def test_rejects_any_filename_outside_the_allowlist(tmp_path: Path):
    tool = WriteSpecTool(tmp_path)
    for bad in ("notes.md", "../evil.md", "PRD.txt", "prd.md", "docs/PRD.md", ""):
        out = await tool.run(filename=bad, content="x")
        assert out.startswith("ERROR:"), f"accepted forbidden filename {bad!r}"
    assert not (tmp_path / "docs").exists()  # nothing was written, no dir created


def test_schema_enumerates_exactly_the_six_artifacts(tmp_path: Path):
    schema = WriteSpecTool(tmp_path).schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "write_spec"
    props = schema["function"]["parameters"]["properties"]
    assert props["filename"]["enum"] == list(SPEC_FILENAMES)
    assert SPEC_FILENAMES == (
        "PRD.md", "HLD.md", "LLD.md", "api.md", "database.md", "risks.md"
    )
    assert schema["function"]["parameters"]["required"] == ["filename", "content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_spec_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.tools.spec_writer'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/tools/spec_writer.py`:

```python
"""write_spec — Act 2's artifact writer.

The only tool that writes inside the repo. It may write exactly six spec
artifacts, always under docs/design/. Overwriting is allowed by design: the
conversation is the source of truth and the files are its artifact. The tool
emits no events — the agent's closing SpeechChunk announces what was written.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.tools.base import Tool

SPEC_FILENAMES: tuple[str, ...] = (
    "PRD.md",
    "HLD.md",
    "LLD.md",
    "api.md",
    "database.md",
    "risks.md",
)


class WriteSpecTool(Tool):
    name = "write_spec"
    description = (
        "Write a design spec artifact to docs/design/ in the repo. Only call "
        "this once the design reasoning is explicit — the spec must record "
        "why choices were made, not just what was chosen. Overwriting an "
        "existing artifact is allowed: the conversation is the source of truth."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "enum": list(SPEC_FILENAMES),
                "description": "Which spec artifact to write",
            },
            "content": {
                "type": "string",
                "description": "Full markdown content of the artifact",
            },
        },
        "required": ["filename", "content"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, filename: str, content: str) -> str:
        if filename not in SPEC_FILENAMES:
            return (
                f"ERROR: '{filename}' is not an allowed spec artifact. "
                f"Allowed filenames: {', '.join(SPEC_FILENAMES)}."
            )
        return await asyncio.to_thread(self._write, filename, content)

    def _write(self, filename: str, content: str) -> str:
        directory = self.root / "docs" / "design"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        verb = "Overwrote" if existed else "Wrote"
        return f"{verb} docs/design/{filename} ({len(content)} characters)."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_spec_writer.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/tools/spec_writer.py tests/test_spec_writer.py
git commit -m "feat: write_spec tool with six-artifact filename allowlist"
```

---

### Task 2: DESIGN_PROMPT (the skeptic policy) + understand-mode guard rule

**Files:**
- Create: `pyrrhon/core/agent/design_prompts.py`
- Modify: `pyrrhon/core/agent/prompts.py`
- Test: `tests/test_design_prompts.py`

**Interfaces:**
- Consumes: `SPEC_FILENAMES` from Task 1; `SYSTEM_PROMPT` from `pyrrhon/core/agent/prompts.py` (M0 Task 7).
- Produces: `design_prompts.DESIGN_PROMPT: str` — the full design-mode policy, injected into history by `Session.set_mode("design")` (Task 4). The prompt encodes: never agree immediately; identify and challenge the weakest assumption with a concrete alternative (the spec's Mongo-vs-Postgres exchange as a style exemplar); one question per turn, short conversational turns; write specs via `write_spec` only after the key choices (data model, interfaces, failure modes, scale) are justified; specs record the reasoning, not just the decisions.

- [ ] **Step 1: Write the failing test**

`tests/test_design_prompts.py`:

```python
from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.agent.prompts import SYSTEM_PROMPT
from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES


def test_design_prompt_encodes_the_skeptic_policy():
    lower = DESIGN_PROMPT.lower()
    assert "never agree" in lower
    assert "weakest assumption" in lower
    assert "one question" in lower


def test_design_prompt_carries_the_mongo_postgres_exemplar_and_artifacts():
    assert "MongoDB" in DESIGN_PROMPT
    assert "Postgres" in DESIGN_PROMPT
    assert "relational" in DESIGN_PROMPT
    for name in SPEC_FILENAMES:
        assert name in DESIGN_PROMPT, f"{name} missing from DESIGN_PROMPT"


def test_design_prompt_demands_reasoning_before_and_inside_specs():
    lower = DESIGN_PROMPT.lower()
    for choice in ("data model", "interfaces", "failure modes", "scale"):
        assert choice in lower, f"key choice '{choice}' missing"
    assert "write_spec" in DESIGN_PROMPT
    assert "reasoning" in lower


def test_understand_prompt_forbids_spec_writing():
    assert "write_spec" in SYSTEM_PROMPT
    assert "/mode design" in SYSTEM_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_design_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.agent.design_prompts'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/agent/design_prompts.py`:

```python
"""Act 2's skeptic policy — injected on top of the base teaching prompt by
Session.set_mode("design"). This is the product's design-mode personality;
edit deliberately."""

from __future__ import annotations

from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES

DESIGN_PROMPT = f"""\
You are now in DESIGN MODE. The user is describing a system they are about to
build. Interrogate the design like a senior architect: Pyrrho's skepticism
applied forward — suspend judgment until a choice is justified.

How you behave:
- NEVER agree with a proposal immediately. Before anything else, identify the
  weakest assumption in what the user just said and challenge it with a
  concrete alternative. Style exemplar:
    User: "Let's use MongoDB."
    You: "Your data looks relational — users, orders, joins. What specific
    benefit are you expecting from Mongo over Postgres here?"
- Ask exactly ONE question per turn. Short conversational turns, not
  questionnaires. Wait for the answer before moving to the next concern.
- Work through the key choices until each one is justified: the data model,
  the interfaces (APIs and boundaries), the failure modes, and the expected
  scale. "Because it's popular" is not a justification; a trade-off argued
  from the actual requirements is.
- Concede when the user's reasoning is sound. You are a skeptic, not a
  contrarian: the goal is explicit reasoning, not winning the argument.

Writing specs:
- Only once the user has justified the key choices above, call the write_spec
  tool. Never write an artifact while the reasoning is still implicit.
- Allowed artifacts: {", ".join(SPEC_FILENAMES)}. Write PRD.md first; write
  the others as the conversation covers their ground.
- Specs must record the *reasoning*, not just the decisions: every significant
  choice lists the alternatives considered and why they lost. A future reader
  should be able to reconstruct the argument, not just the conclusion.
- Overwriting an earlier version of an artifact is fine — the conversation is
  the source of truth and the files are its artifact.
- After write_spec succeeds, tell the user in one short sentence what you
  wrote and where.
"""
```

Then modify `pyrrhon/core/agent/prompts.py`: append one bullet to the end of
the `Hard rules:` list inside `SYSTEM_PROMPT`. As of M0 the last bullet is
`- Prefer citing a few exact lines over quoting long blocks.`; per the drift
warning, locate the current end of the hard-rules list and append after it:

```text
- The write_spec tool exists but is design-mode only: in understand mode do
  not write spec files. If the user starts designing something new, suggest
  switching with /mode design.
```

(Keep it inside the triple-quoted `SYSTEM_PROMPT` string, same indentation as
the other bullets.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_design_prompts.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole suite (the prompt edit touches every agent test's system prompt)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent/design_prompts.py pyrrhon/core/agent/prompts.py tests/test_design_prompts.py
git commit -m "feat: design-mode skeptic prompt; understand mode forbids write_spec"
```

---

### Task 3: extract_question + AskUser emission in the agent loop

**Files:**
- Modify: `pyrrhon/core/agent/loop.py`
- Test: `tests/test_extract_question.py`

**Interfaces:**
- Consumes: `Agent` and its `run_turn` final-text branch (M0 Task 8, since reshaped by M1's grounding gate — revalidate); `AskUser`, `SpeechChunk` from `pyrrhon/core/events.py` (M0 Task 4); `FakeLLM` (tests only).
- Produces:
  - `loop.extract_question(text: str) -> str | None` — pure function: if the stripped text ends with `?`, return its last sentence (split on whitespace following `.`/`!`/`?`); otherwise `None`. Text ending in anything but a bare `?` (e.g. `?)`) returns `None` — deliberate, keep it dumb and predictable.
  - `Agent.__init__` gains kwarg `mode: str = "understand"` stored as mutable `self.mode` (Session reassigns it on `/mode` switches; append the kwarg after the current M1–M5 signature per the drift warning).
  - Contract: in design mode, when the final reply text ends with a question mark — which by construction is the branch with no tool calls — `run_turn` additionally yields `AskUser(question=<last sentence>)` after the `SpeechChunk` and any `Citation`s, so channels can render/say the question distinctly.

- [ ] **Step 1: Write the failing test**

`tests/test_extract_question.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.loop import Agent, extract_question
from pyrrhon.core.events import AskUser
from pyrrhon.core.providers.llm import LLMReply
from tests.helpers import FakeLLM


def test_none_when_not_a_question():
    assert extract_question("The data is relational.") is None


def test_returns_single_sentence_question():
    assert extract_question("Why Mongo over Postgres?") == "Why Mongo over Postgres?"


def test_returns_last_sentence_only():
    text = (
        "Your data looks relational — users, orders, joins. "
        "What specific benefit are you expecting from Mongo over Postgres here?"
    )
    assert extract_question(text) == (
        "What specific benefit are you expecting from Mongo over Postgres here?"
    )


def test_trailing_whitespace_is_tolerated():
    assert extract_question("Ready to proceed?  \n") == "Ready to proceed?"


def test_question_mid_text_with_statement_ending_is_none():
    assert extract_question("Why Mongo? Because you said so.") is None


def make_agent(replies, mode: str) -> Agent:
    return Agent(
        llm=FakeLLM(replies),
        tools=[],
        system_prompt="You are a test agent.",
        repo_root=Path("."),
        mode=mode,
    )


async def collect(agent: Agent, text: str) -> list:
    return [event async for event in agent.run_turn([], text)]


async def test_design_mode_question_reply_yields_askuser():
    question = "What specific benefit are you expecting from Mongo over Postgres here?"
    agent = make_agent([LLMReply(text=f"Your data looks relational. {question}")], mode="design")
    events = await collect(agent, "let's use mongo")
    assert AskUser(question=question) in events
    # AskUser comes after the SpeechChunk (channels speak first, then highlight):
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("AskUser") > kinds.index("SpeechChunk")


async def test_understand_mode_never_yields_askuser():
    agent = make_agent([LLMReply(text="Want to see the code?")], mode="understand")
    events = await collect(agent, "explain app.py")
    assert not [e for e in events if isinstance(e, AskUser)]


async def test_design_mode_statement_reply_yields_no_askuser():
    agent = make_agent([LLMReply(text="Postgres it is. Good reasoning.")], mode="design")
    events = await collect(agent, "here's my justification")
    assert not [e for e in events if isinstance(e, AskUser)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_extract_question.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_question' from 'pyrrhon.core.agent.loop'`

- [ ] **Step 3: Write minimal implementation**

Four edits to `pyrrhon/core/agent/loop.py`:

1. Ensure `re` is imported and `AskUser` is in the events import:

```python
import re

from pyrrhon.core.events import (
    AskUser,
    Event,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
)
```

2. Add the pure function at module level (above `class Agent`):

```python
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def extract_question(text: str) -> str | None:
    """Return the reply's final sentence if the reply ends with a question.

    Pure and deliberately dumb: only a stripped trailing '?' counts, and the
    "last sentence" is whatever follows the final .!?-plus-whitespace
    boundary. Channels use the result to render/say Pyrrhon's Socratic
    question distinctly (the AskUser event).
    """
    stripped = text.strip()
    if not stripped.endswith("?"):
        return None
    return _SENTENCE_BOUNDARY.split(stripped)[-1]
```

3. In `Agent.__init__`, append the kwarg `mode: str = "understand"` after the
   current final parameter (as of M5 that is whatever came after
   `max_tool_rounds` / `grounding_gate` / `allow_retry` / `deep_llm` —
   revalidate) and store it:

```python
        self.mode = mode
```

4. In `run_turn`, the final-text branch (the one taken when
   `reply.tool_calls` is empty — M1's grounding gate rewrote it, so graft
   these lines after the branch's last `yield`, immediately before its
   `return`):

```python
                if self.mode == "design":
                    question = extract_question(text)
                    if question is not None:
                        yield AskUser(question=question)
                return
```

The M0-shaped branch, for orientation, becomes:

```python
            if not reply.tool_calls:
                text = reply.text or "(no answer)"
                history.append({"role": "assistant", "content": text})
                yield SpeechChunk(text=text)
                for citation in extract_citations(text, self.repo_root):
                    yield citation
                if self.mode == "design":
                    question = extract_question(text)
                    if question is not None:
                        yield AskUser(question=question)
                return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_extract_question.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the whole suite (guard against loop regressions)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent/loop.py tests/test_extract_question.py
git commit -m "feat: design-mode AskUser emission via pure extract_question"
```

---

### Task 4: Session.set_mode, /mode command, write_spec registration

**Files:**
- Modify: `pyrrhon/core/session.py`, `pyrrhon/commands/registry.py`, `pyrrhon/repl.py` (and the TUI's `CommandContext` construction site, M2 — revalidate location)
- Create: `pyrrhon/commands/mode_cmd.py`
- Test: `tests/test_session_mode.py`, `tests/test_mode_command.py`

**Interfaces:**
- Consumes: `Session(agent)` with `history`, `mode` (M1–M5); `command(name, help_text)` / `dispatch(line, ctx)` / `CommandContext(repo_root, agent, ui)` (M2); `DESIGN_PROMPT` (Task 2); `Agent.mode` (Task 3); `WriteSpecTool` (Task 1); `build_agent` (M0 Task 9).
- Produces:
  - `Session.set_mode(mode: str) -> None` — validates against `{"understand", "design"}` (raises `ValueError` otherwise); same-mode calls are no-ops; switching injects a system-role message into `history`: design → the full `DESIGN_PROMPT`, understand → the one-line marker `session.UNDERSTAND_MARKER = "Return to understand mode."`. The base `SYSTEM_PROMPT` from turn one always stays underneath — if `history` is still empty, the base prompt is inserted first so the mode message layers on top. Also sets `self.agent.mode`.
  - `CommandContext` gains a `session: Session` field (update both construction sites: `pyrrhon/repl.py` and the TUI, passing the live session).
  - `/mode understand|design` registered command in `mode_cmd.py`; no argument prints the current mode; a bad argument prints the `ValueError` as `ERROR: ...` without changing state.
  - `build_agent` registers `WriteSpecTool(repo_root)` unconditionally — always registered; `DESIGN_PROMPT` is what instructs its use, and the understand-mode prompt (Task 2) says not to write spec files.

- [ ] **Step 1: Write the failing tests**

`tests/test_session_mode.py`:

```python
from pathlib import Path

import pytest

from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.session import UNDERSTAND_MARKER, Session
from tests.helpers import FakeLLM


def make_session() -> Session:
    agent = Agent(
        llm=FakeLLM([]),
        tools=[],
        system_prompt="BASE TEACHING PROMPT",
        repo_root=Path("."),
    )
    return Session(agent)


def test_set_mode_rejects_unknown_mode():
    session = make_session()
    with pytest.raises(ValueError, match="prophecy"):
        session.set_mode("prophecy")
    assert session.mode == "understand"


def test_switch_to_design_layers_prompt_on_top_of_base():
    session = make_session()
    session.set_mode("design")
    assert session.mode == "design"
    assert session.agent.mode == "design"
    assert session.history[0] == {"role": "system", "content": "BASE TEACHING PROMPT"}
    assert session.history[1] == {"role": "system", "content": DESIGN_PROMPT}


def test_switch_back_to_understand_injects_marker_not_a_second_base():
    session = make_session()
    session.set_mode("design")
    session.set_mode("understand")
    assert session.mode == "understand"
    assert session.agent.mode == "understand"
    assert session.history[-1] == {"role": "system", "content": UNDERSTAND_MARKER}
    base_count = sum(
        1 for m in session.history if m["content"] == "BASE TEACHING PROMPT"
    )
    assert base_count == 1  # the turn-one base prompt stays, exactly once


def test_setting_the_current_mode_is_a_noop():
    session = make_session()
    session.set_mode("understand")
    assert session.history == []
    assert session.mode == "understand"
```

`tests/test_mode_command.py`:

```python
from pathlib import Path

import pyrrhon.commands.mode_cmd  # noqa: F401  (registers /mode)
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.session import Session
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM


class StubUI:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, text) -> None:
        self.lines.append(str(text))


def make_ctx(tmp_path: Path) -> CommandContext:
    agent = Agent(
        llm=FakeLLM([]), tools=[], system_prompt="BASE", repo_root=tmp_path
    )
    session = Session(agent)
    return CommandContext(
        repo_root=tmp_path, agent=agent, ui=StubUI(), session=session
    )


def test_mode_command_switches_to_design(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    out = dispatch("/mode design", ctx)
    assert ctx.session.mode == "design"
    assert "design" in out


def test_mode_command_rejects_garbage_without_changing_state(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    out = dispatch("/mode prophecy", ctx)
    assert ctx.session.mode == "understand"
    assert out.startswith("ERROR:")


def test_mode_command_without_args_reports_current_mode(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    out = dispatch("/mode", ctx)
    assert "understand" in out
    assert ctx.session.mode == "understand"


def test_build_agent_always_registers_write_spec(tmp_path: Path):
    agent = build_agent(tmp_path, llm=FakeLLM([]))
    assert "write_spec" in agent.tools
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session_mode.py tests/test_mode_command.py -v`
Expected: FAIL — `ImportError: cannot import name 'UNDERSTAND_MARKER' from 'pyrrhon.core.session'` and `ModuleNotFoundError: No module named 'pyrrhon.commands.mode_cmd'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/session.py`, add the import, the module constants, and the
method (the class body below shows only what M6 adds — merge into the
existing `Session`):

```python
from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT

VALID_MODES: frozenset[str] = frozenset({"understand", "design"})
UNDERSTAND_MARKER = "Return to understand mode."


class Session:
    # ... existing M1–M5 body (agent, history, mode, abort_current_turn, ...) ...

    def set_mode(self, mode: str) -> None:
        """Switch understand <-> design by layering a system message.

        The base teaching prompt from turn one always stays underneath; the
        injected message sits on top of the history. Design gets the full
        skeptic policy; understand gets a one-line marker (the base prompt
        already carries the teaching policy, so no re-injection is needed).
        """
        if mode not in VALID_MODES:
            raise ValueError(
                f"Unknown mode '{mode}'. Valid modes: "
                f"{', '.join(sorted(VALID_MODES))}."
            )
        if mode == self.mode:
            return
        if not self.history:
            # First run_turn normally injects the base prompt; if the user
            # switches mode before saying anything, inject it now so the
            # mode message never becomes the conversation's foundation.
            self.history.append(
                {"role": "system", "content": self.agent.system_prompt}
            )
        self.mode = mode
        self.agent.mode = mode
        content = DESIGN_PROMPT if mode == "design" else UNDERSTAND_MARKER
        self.history.append({"role": "system", "content": content})
```

(Note: M0's `run_turn` injects the base prompt only when `history` is empty,
so a pre-populated history is never double-seeded. Revalidate that guard
still exists post-M5.)

In `pyrrhon/commands/registry.py`, add the field to `CommandContext` (a
dataclass as of M2 — revalidate):

```python
from pyrrhon.core.session import Session


@dataclass
class CommandContext:
    repo_root: Path
    agent: Agent
    ui: Any
    mcp: "MCPManager | None" = None   # M5's field — keep it
    session: "Session | None" = None  # M6 addition: defaulted, additive
```

(Additive-with-default, like M5's `mcp` and M7's `plugins` — existing
construction sites keep working; new ones pass `session=`.)

Update every `CommandContext(...)` construction site — `pyrrhon/repl.py` and
the TUI (one each as of M5) — to pass `session=<the live Session>`.

`pyrrhon/commands/mode_cmd.py`:

```python
"""/mode — switch between understand and design mode."""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command


@command("mode", "Switch mode: /mode understand|design")
def mode_cmd(args: str, ctx: CommandContext) -> str:
    if ctx.session is None:
        return "ERROR: no active session."
    mode = args.strip()
    if not mode:
        return f"Current mode: {ctx.session.mode}. Usage: /mode understand|design"
    try:
        ctx.session.set_mode(mode)
    except ValueError as exc:
        return f"ERROR: {exc}"
    return f"Mode set to {mode}."
```

(M2's dispatch contract: the handler returns the response string and the
caller renders it — a handler returning `None` would make `dispatch` return
`None`, which callers treat as "not a command" and forward to the LLM.)

Ensure the module is imported wherever M2 imports command modules for
registration (e.g. add `from pyrrhon.commands import mode_cmd  # noqa: F401`
to that import block — revalidate the mechanism).

In `pyrrhon/repl.py`, `build_agent` adds the tool unconditionally:

```python
from pyrrhon.core.tools.spec_writer import WriteSpecTool
```

and append `WriteSpecTool(repo_root)` to the `tools` list. Always registered:
`DESIGN_PROMPT` is what instructs its use, and the understand-mode prompt
(Task 2) forbids writing spec files.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_mode.py tests/test_mode_command.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the whole suite (the CommandContext field touches M2–M5 command tests)**

Run: `uv run pytest -q`
Expected: all tests pass — if M2–M5 command tests construct `CommandContext` directly, add `session=` there too (mechanical fix, part of this task).

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/session.py pyrrhon/commands pyrrhon/repl.py pyrrhon/tui tests/test_session_mode.py tests/test_mode_command.py
git commit -m "feat: /mode switching with layered design prompt; register write_spec"
```

---

### Task 5: End-to-end scripted design session (challenge → justify → PRD.md)

**Files:**
- Test: `tests/test_design_session_e2e.py`

**Interfaces:**
- Consumes: everything above — `build_agent` (with `WriteSpecTool` registered, Task 4), `Session.set_mode` (Task 4), `AskUser` emission (Task 3), `DESIGN_PROMPT` (Task 2), `WriteSpecTool` on disk behavior (Task 1); `FakeLLM`, `LLMReply`, `ToolCall`, events.
- Produces: the executable proof of VISION.md success criterion 4's shape: the model pushes back before writing (round 1: challenge, no `write_spec`), and after justification produces a `PRD.md` on disk that records reasoning. No production code in this task — if any assertion here needs one, a prior task was implemented wrong; fix it there.

- [ ] **Step 1: Write the failing test**

`tests/test_design_session_e2e.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.events import AskUser, SpeechChunk, ToolCallStarted
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import Session
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

CHALLENGE = (
    "Your data looks relational — users, orders, joins. "
    "What specific benefit are you expecting from Mongo over Postgres here?"
)

PRD_CONTENT = """\
# PRD — Order Service

## Problem
Small merchants need order tracking with reliable payment state.

## Decision: Postgres over MongoDB
Proposed: MongoDB. Challenged: the data is relational (users, orders,
line-item joins). Justification given: the team knows Postgres, and payment
state transitions need transactional integrity. Alternatives considered:
MongoDB (rejected — no benefit identified for relational data), DynamoDB
(rejected — no team experience, same join problem). Decision: Postgres.
"""


async def test_scripted_design_session_challenges_then_writes_prd(tmp_path: Path):
    fake = FakeLLM(
        [
            # Round 1: the model challenges the weakest assumption — no tools.
            LLMReply(text=CHALLENGE),
            # Round 2: justification received → write the PRD...
            LLMReply(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="write_spec",
                        arguments={"filename": "PRD.md", "content": PRD_CONTENT},
                    ),
                )
            ),
            # ...then announce it in speech.
            LLMReply(
                text="I've written the PRD at docs/design/PRD.md — Postgres it is."
            ),
        ]
    )
    agent = build_agent(tmp_path, llm=fake)
    session = Session(agent)
    session.set_mode("design")
    assert {"role": "system", "content": DESIGN_PROMPT} in session.history

    # --- Round 1: proposal → challenge, and nothing gets written ---
    round1 = [
        e
        async for e in agent.run_turn(
            session.history, "Let's build the order service on MongoDB."
        )
    ]
    spec_calls = [
        e for e in round1 if isinstance(e, ToolCallStarted) and e.name == "write_spec"
    ]
    assert spec_calls == []  # pushback happens BEFORE any artifact exists
    assert not (tmp_path / "docs" / "design" / "PRD.md").exists()
    assert SpeechChunk(text=CHALLENGE) in round1
    assert AskUser(
        question=(
            "What specific benefit are you expecting from Mongo over Postgres here?"
        )
    ) in round1

    # --- Round 2: justification → write_spec → PRD.md on disk ---
    round2 = [
        e
        async for e in agent.run_turn(
            session.history,
            "Fair. The data is relational and payment state needs transactions "
            "— Postgres, and here's the reasoning for the record.",
        )
    ]
    started = [e for e in round2 if isinstance(e, ToolCallStarted)]
    assert started and started[0].name == "write_spec"
    prd = tmp_path / "docs" / "design" / "PRD.md"
    assert prd.read_text(encoding="utf-8") == PRD_CONTENT
    speech = [e for e in round2 if isinstance(e, SpeechChunk)]
    assert "docs/design/PRD.md" in speech[-1].text
```

(Repo root is `tmp_path`, not the checked-in fixture repo, because this test
writes real files. The scripted texts contain no `path:line` claims, so M1's
grounding gate passes them through untouched — `docs/design/PRD.md` has no
`:line` suffix and does not match the citation pattern.)

- [ ] **Step 2: Run the test (integration — its implementation already exists)**

Run: `uv run pytest tests/test_design_session_e2e.py -v`
Expected: 1 passed, immediately, because Tasks 1–4 already provide every piece it exercises. If it fails, the failure names the task to fix (e.g. missing `AskUser` → Task 3; missing `write_spec` in `agent.tools` → Task 4) — fix it *there*; this task adds no production code.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (the M0–M5 suites plus the 25 tests added by this plan).

- [ ] **Step 4: Manual smoke test (needs a provider key set)**

Run `uv run pyrrhon <some scratch repo>`, then:

1. `/mode` → prints `Current mode: understand`.
2. `/mode design` → confirmation printed; status bar (TUI) shows design mode.
3. Say/type: "I want to build an order tracker. Let's use MongoDB." → confirm Pyrrhon pushes back with a concrete alternative and exactly one question, and that the question is visibly highlighted (the `AskUser` rendering); confirm no file appears under `docs/design/`.
4. Justify the choice (or accept its counter) over a couple of turns → confirm it eventually calls `write_spec`, `docs/design/PRD.md` exists, records the reasoning (alternatives and why they lost), and the closing sentence announces the write.
5. `/mode understand` → confirm it answers repo questions normally and declines to write specs.

- [ ] **Step 5: Commit**

```bash
git add tests/test_design_session_e2e.py
git commit -m "test: end-to-end scripted design session (challenge then PRD.md)"
```

---

## Definition of Done (M6)

Maps to VISION.md success criterion 4 — *"In design mode, it pushes back on at least one questionable choice before writing a spec, and produces a `PRD.md` I'd actually keep."*

- `uv run pytest` fully green, including `tests/test_design_session_e2e.py`: the scripted session proves the shape of criterion 4 mechanically — round 1 contains a challenge and zero `write_spec` calls; the PRD lands on disk only after justification.
- **"Pushes back before writing":** live smoke test (Task 5 Step 4) shows Pyrrhon challenging the weakest assumption with a concrete alternative (Mongo-vs-Postgres-style) and asking one question at a time, rendered distinctly via `AskUser`, before any artifact exists under `docs/design/`.
- **"A PRD.md I'd actually keep":** the written spec records the reasoning — alternatives considered and why they lost — not just the decisions, per `DESIGN_PROMPT`; judged by reading the smoke-test PRD.
- `write_spec` can only produce the six allowlisted artifacts under `docs/design/` (enforced in code; `tests/test_spec_writer.py`), and understand mode declines to write specs at all.
- `/mode understand|design` round-trips: the base teaching prompt from turn one stays; mode messages layer on top (`tests/test_session_mode.py`).
- `core/` still has no imports from channels (verify: `grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/` returns nothing).

With criterion 4 demonstrable, all four VISION.md success criteria have a home: 1–3 landed with M3 (voice, barge-in, honest unknowns) and 4 lands here. What remains for v1 is M7 (plugin loader + optional GUI spike).
