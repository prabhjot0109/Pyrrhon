# Pyrrhon M0 — Grounded Text REPL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A text REPL where you point Pyrrhon at a repo, ask questions, and get conversational, first-principles answers whose `file:line` citations are extracted and displayed — proving the agent core before voice or TUI exist.

**Architecture:** Headless `core/` (events, provider adapter, tools, agent loop, citation extraction) with a thin `repl.py` channel on top. `core/` imports nothing from UI/audio code — this is the permanent seam for TUI (M2), voice (M3), and a future GUI. One OpenAI-compatible LLM adapter covers Groq/OpenRouter/Cerebras/Gemini/OpenAI.

**Tech Stack:** Python ≥ 3.12, uv, pydantic v2, openai SDK (as OpenAI-compatible client), rich, pytest + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` (M0 section + amendments of 2026-07-03).

## Global Constraints

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.**
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then.
- No grounding *verification* in M0 (that is M1); M0 only extracts citations for files that exist.
- Commit after every task (green tests only).

## File Structure (locked in by this plan)

```text
pyrrhon/
├── __init__.py              # __version__
├── __main__.py              # python -m pyrrhon
├── cli.py                   # argparse entry point → repl
├── repl.py                  # rich-based text REPL (thin channel)
├── commands/
│   ├── __init__.py
│   └── init_cmd.py          # /init: scaffold .pyrrhon/soul.md
├── config/
│   ├── __init__.py
│   └── settings.py          # TOML load/merge, provider presets, model slots
└── core/
    ├── __init__.py
    ├── events.py            # SpeechChunk, Citation, ToolCall*, ... (the contract)
    ├── agent/
    │   ├── __init__.py
    │   ├── loop.py          # Agent: tool-calling loop → event stream
    │   ├── prompts.py       # SYSTEM_PROMPT (teaching policy)
    │   └── soul.py          # load .pyrrhon/*.md into system prompt
    ├── grounding/
    │   ├── __init__.py
    │   └── citations.py     # extract_citations(text, root)
    ├── providers/
    │   ├── __init__.py
    │   └── llm.py           # LLMReply, ToolCall, OpenAICompatLLM, create_llm
    └── tools/
        ├── __init__.py
        ├── base.py          # Tool ABC + schema()
        └── repo.py          # ReadFileTool, GrepTool, GlobTool

tests/
├── helpers.py               # FakeLLM
├── fixtures/sample_repo/    # tiny repo the tool/agent tests run against
│   ├── app.py
│   ├── utils/helpers.py
│   └── README.md
├── test_cli.py
├── test_settings.py
├── test_llm_adapter.py
├── test_repo_tools.py
├── test_citations.py
├── test_soul.py
├── test_agent_loop.py
└── test_init_and_repl.py
```

Later milestones (each gets its own plan): M1 grounding gate (split-path recovery) + eval + `remember`/memory.md, M2 Textual TUI, M3 Pipecat voice (barge-in, `TruncateSpeech` history sync, turn cancellation), M4 tree-sitter/git/web tools, M5 MCP + fallbacks, M6 design mode, M7 plugin loader.

---

### Task 1: Package scaffold, dependencies, CLI entry point

**Files:**
- Modify: `pyproject.toml`
- Create: `pyrrhon/__init__.py`, `pyrrhon/__main__.py`, `pyrrhon/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `pyrrhon.__version__: str`; `pyrrhon.cli.main(argv: list[str] | None = None) -> None` (parses `repo` positional defaulting to `"."`, `--version`); console script `pyrrhon`.

- [ ] **Step 1: Add dependencies and build config**

Run:

```bash
uv add openai pydantic rich
uv add --dev pytest pytest-asyncio respx
```

Then edit `pyproject.toml` so the full file reads (keep the dependency versions uv wrote; only add the missing sections):

```toml
[project]
name = "pyrrhon"
version = "0.1.0"
description = "A voice-first senior-engineer agent for understanding and designing software."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    # (versions as written by `uv add`)
    "openai",
    "pydantic",
    "rich",
]

[project.scripts]
pyrrhon = "pyrrhon.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling"

[tool.hatch.build.targets.wheel]
packages = ["pyrrhon"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[dependency-groups]
dev = [
    # (versions as written by `uv add --dev`)
    "pytest",
    "pytest-asyncio",
    "respx",
]
```

Run: `uv sync` — Expected: resolves and installs without error.

- [ ] **Step 2: Write the failing test**

`tests/test_cli.py`:

```python
import pytest

from pyrrhon import __version__
from pyrrhon.cli import main


def test_version_is_current():
    assert __version__ == "0.1.0"


def test_cli_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'pyrrhon'`

- [ ] **Step 4: Write minimal implementation**

`pyrrhon/__init__.py`:

```python
__version__ = "0.1.0"
```

`pyrrhon/cli.py`:

```python
"""Command-line entry point: `pyrrhon [repo-path]`."""

from __future__ import annotations

import argparse

from pyrrhon import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pyrrhon",
        description="Talk to a codebase like a senior engineer is sitting next to you.",
    )
    parser.add_argument("repo", nargs="?", default=".", help="Path to the repo to discuss")
    parser.add_argument("--version", action="version", version=f"pyrrhon {__version__}")
    args = parser.parse_args(argv)

    # Imported lazily so `--version` works before the REPL exists (Task 9 wires it).
    from pyrrhon.repl import run_repl

    run_repl(args.repo)
```

`pyrrhon/__main__.py`:

```python
from pyrrhon.cli import main

main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock pyrrhon tests
git commit -m "feat: scaffold pyrrhon package with CLI entry point"
```

---

### Task 2: Settings — TOML load/merge, provider presets, model slots

**Files:**
- Create: `pyrrhon/config/__init__.py` (empty), `pyrrhon/config/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ModelSlot(BaseModel)` with `provider: str`, `model: str`
  - `ProviderConfig(BaseModel)` with `base_url: str | None`, `api_key_env: str`
  - `BUILTIN_PROVIDERS: dict[str, ProviderConfig]` (openai, groq, openrouter, cerebras, gemini)
  - `Settings(BaseModel)` with `fast: ModelSlot` (default groq/llama-3.3-70b-versatile), `deep: ModelSlot | None`, `providers: dict[str, ProviderConfig]`, property `deep_slot -> ModelSlot` (falls back to `fast`), method `provider_for(slot) -> ProviderConfig` (raises `KeyError` for unknown provider)
  - `load_settings(repo_root: Path, home: Path | None = None) -> Settings` — merges `<home>/.pyrrhon/config.toml` then `<repo>/.pyrrhon.toml` (repo wins per top-level key); missing files are fine.

- [ ] **Step 1: Write the failing test**

`tests/test_settings.py`:

```python
from pathlib import Path

import pytest

from pyrrhon.config.settings import ModelSlot, Settings, load_settings


def test_defaults_when_no_files(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.fast.provider == "groq"
    assert settings.deep is None
    assert settings.deep_slot == settings.fast  # unambiguous fallback rule


def test_repo_config_overrides_global(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".pyrrhon").mkdir(parents=True)
    (home / ".pyrrhon" / "config.toml").write_text(
        '[fast]\nprovider = "openai"\nmodel = "gpt-4.1-mini"\n', encoding="utf-8"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        '[fast]\nprovider = "cerebras"\nmodel = "llama3.3-70b"\n', encoding="utf-8"
    )
    settings = load_settings(repo_root=repo, home=home)
    assert settings.fast.provider == "cerebras"


def test_builtin_provider_lookup_and_unknown_raises(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    provider = settings.provider_for(settings.fast)
    assert provider.api_key_env == "GROQ_API_KEY"
    with pytest.raises(KeyError):
        settings.provider_for(ModelSlot(provider="doesnotexist", model="x"))


def test_custom_provider_in_config(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        "[providers.myproxy]\n"
        'base_url = "http://localhost:8000/v1"\n'
        'api_key_env = "MYPROXY_KEY"\n'
        "[fast]\n"
        'provider = "myproxy"\nmodel = "local-model"\n',
        encoding="utf-8",
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.provider_for(settings.fast).base_url == "http://localhost:8000/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.config'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/config/settings.py`:

```python
"""Load and merge Pyrrhon settings: global ~/.pyrrhon/config.toml then <repo>/.pyrrhon.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class ModelSlot(BaseModel):
    provider: str
    model: str


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str


BUILTIN_PROVIDERS: dict[str, ProviderConfig] = {
    "openai": ProviderConfig(base_url=None, api_key_env="OPENAI_API_KEY"),
    "groq": ProviderConfig(
        base_url="https://api.groq.com/openai/v1", api_key_env="GROQ_API_KEY"
    ),
    "openrouter": ProviderConfig(
        base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY"
    ),
    "cerebras": ProviderConfig(
        base_url="https://api.cerebras.ai/v1", api_key_env="CEREBRAS_API_KEY"
    ),
    "gemini": ProviderConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
    ),
}


class Settings(BaseModel):
    fast: ModelSlot = ModelSlot(provider="groq", model="llama-3.3-70b-versatile")
    deep: ModelSlot | None = None
    providers: dict[str, ProviderConfig] = {}

    @property
    def deep_slot(self) -> ModelSlot:
        # Spec rule: the deep slot falls back to the fast slot when unset.
        return self.deep or self.fast

    def provider_for(self, slot: ModelSlot) -> ProviderConfig:
        if slot.provider in self.providers:
            return self.providers[slot.provider]
        if slot.provider in BUILTIN_PROVIDERS:
            return BUILTIN_PROVIDERS[slot.provider]
        raise KeyError(
            f"Unknown provider '{slot.provider}'. Add [providers.{slot.provider}] "
            f"to .pyrrhon.toml or use one of: {', '.join(BUILTIN_PROVIDERS)}"
        )


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings(repo_root: Path, home: Path | None = None) -> Settings:
    home = home or Path.home()
    merged = {
        **_read_toml(home / ".pyrrhon" / "config.toml"),
        **_read_toml(repo_root / ".pyrrhon.toml"),
    }
    return Settings.model_validate(merged)
```

Create empty `pyrrhon/config/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/config tests/test_settings.py
git commit -m "feat: settings with TOML merge, provider presets, model slots"
```

---

### Task 3: LLM adapter — OpenAI-compatible client + factory

**Files:**
- Create: `pyrrhon/core/__init__.py` (empty), `pyrrhon/core/providers/__init__.py` (empty), `pyrrhon/core/providers/llm.py`
- Test: `tests/test_llm_adapter.py`

**Interfaces:**
- Consumes: `ModelSlot`, `Settings` from Task 2.
- Produces:
  - `ToolCall` frozen dataclass: `id: str`, `name: str`, `arguments: dict`
  - `LLMReply` frozen dataclass: `text: str | None = None`, `tool_calls: tuple[ToolCall, ...] = ()`
  - `class OpenAICompatLLM`: `__init__(model: str, api_key: str, base_url: str | None = None)`; `async chat(messages: list[dict], tools: list[dict] | None = None) -> LLMReply`
  - `create_llm(slot: ModelSlot, settings: Settings) -> OpenAICompatLLM` — resolves provider + env key, raises `MissingAPIKeyError` if the env var is unset/empty
  - `class MissingAPIKeyError(RuntimeError)`
- Note for later tasks: anything with the same duck-typed `async chat(...)` (e.g. `FakeLLM`) is a valid LLM for the agent.

- [ ] **Step 1: Write the failing test**

`tests/test_llm_adapter.py`:

```python
import httpx
import pytest
import respx

from pyrrhon.config.settings import ModelSlot, Settings
from pyrrhon.core.providers.llm import MissingAPIKeyError, OpenAICompatLLM, create_llm

BASE = "https://api.groq.com/openai/v1"


def _completion(message: dict) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
    }


@respx.mock
async def test_chat_returns_text():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_completion({"role": "assistant", "content": "hi there"})
        )
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    reply = await llm.chat([{"role": "user", "content": "hello"}])
    assert reply.text == "hi there"
    assert reply.tool_calls == ()


@respx.mock
async def test_chat_parses_tool_calls():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "app.py"}'},
            }
        ],
    }
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(message))
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    reply = await llm.chat([{"role": "user", "content": "read app.py"}])
    assert reply.tool_calls[0].name == "read_file"
    assert reply.tool_calls[0].arguments == {"path": "app.py"}


def test_create_llm_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings = Settings()
    with pytest.raises(MissingAPIKeyError, match="GROQ_API_KEY"):
        create_llm(ModelSlot(provider="groq", model="m"), settings)


def test_create_llm_uses_provider_base_url(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    settings = Settings()
    llm = create_llm(ModelSlot(provider="groq", model="m"), settings)
    assert llm.model == "m"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/providers/llm.py`:

```python
"""Provider-agnostic LLM access via the OpenAI-compatible chat completions API.

One adapter covers OpenAI, Groq, OpenRouter, Cerebras, and Gemini's compat
endpoint — a new provider is a config entry, not new code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from openai import AsyncOpenAI

from pyrrhon.config.settings import ModelSlot, Settings


class MissingAPIKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class LLMReply:
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class OpenAICompatLLM:
    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        calls = tuple(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments or "{}"),
            )
            for tc in (message.tool_calls or ())
        )
        return LLMReply(text=message.content, tool_calls=calls)


def create_llm(slot: ModelSlot, settings: Settings) -> OpenAICompatLLM:
    provider = settings.provider_for(slot)
    api_key = os.environ.get(provider.api_key_env, "")
    if not api_key:
        raise MissingAPIKeyError(
            f"Set {provider.api_key_env} to use provider '{slot.provider}'."
        )
    return OpenAICompatLLM(model=slot.model, api_key=api_key, base_url=provider.base_url)
```

Create empty `pyrrhon/core/__init__.py` and `pyrrhon/core/providers/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_adapter.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core tests/test_llm_adapter.py
git commit -m "feat: OpenAI-compatible LLM adapter with provider factory"
```

---

### Task 4: Event types + FakeLLM test helper

**Files:**
- Create: `pyrrhon/core/events.py`, `tests/helpers.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: `LLMReply` from Task 3 (in `FakeLLM`).
- Produces:
  - Frozen dataclasses in `pyrrhon.core.events`: `SpeechChunk(text: str)`, `ScreenArtifact(kind: str, content: str)`, `Citation(file: str, line: int | None = None, snippet: str | None = None)`, `ToolCallStarted(name: str, args: dict)`, `ToolCallFinished(name: str, result_preview: str)`, `AskUser(question: str)`; type alias `Event` (union of all six)
  - `tests.helpers.FakeLLM(replies: list[LLMReply])` with `async chat(messages, tools=None) -> LLMReply` popping replies in order and recording `self.calls: list[dict]` (each `{"messages": [...], "tools": ...}`)

- [ ] **Step 1: Write the failing test**

`tests/test_events.py`:

```python
from pyrrhon.core.events import Citation, SpeechChunk
from pyrrhon.core.providers.llm import LLMReply
from tests.helpers import FakeLLM


def test_events_are_immutable_values():
    chunk = SpeechChunk(text="hello")
    assert chunk.text == "hello"
    assert Citation(file="app.py", line=3) == Citation(file="app.py", line=3)


async def test_fake_llm_pops_replies_in_order_and_records_calls():
    fake = FakeLLM([LLMReply(text="first"), LLMReply(text="second")])
    first = await fake.chat([{"role": "user", "content": "a"}])
    second = await fake.chat([{"role": "user", "content": "b"}], tools=[{"x": 1}])
    assert (first.text, second.text) == ("first", "second")
    assert len(fake.calls) == 2
    assert fake.calls[1]["tools"] == [{"x": 1}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `pyrrhon.core.events`, no `tests.helpers`)

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/events.py`:

```python
"""The event contract between the headless core and every channel (REPL, TUI, voice, GUI).

Channels subscribe to this stream and render it however they like; the core
never knows who is listening.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechChunk:
    """Speakable prose — streamed to TTS in M3, printed in text channels."""

    text: str


@dataclass(frozen=True)
class ScreenArtifact:
    """Screen-only content (code, path lists, diagrams) — never spoken."""

    kind: str  # "code" | "paths" | "markdown"
    content: str


@dataclass(frozen=True)
class Citation:
    """A source location backing a claim. `file` is repo-relative, POSIX style."""

    file: str
    line: int | None = None
    snippet: str | None = None


@dataclass(frozen=True)
class ToolCallStarted:
    name: str
    args: dict


@dataclass(frozen=True)
class ToolCallFinished:
    name: str
    result_preview: str


@dataclass(frozen=True)
class AskUser:
    """Pyrrhon asking the user a (Socratic) question."""

    question: str


Event = (
    SpeechChunk
    | ScreenArtifact
    | Citation
    | ToolCallStarted
    | ToolCallFinished
    | AskUser
)
```

`tests/helpers.py`:

```python
"""Test doubles shared across the suite."""

from __future__ import annotations

from pyrrhon.core.providers.llm import LLMReply


class FakeLLM:
    """Duck-typed stand-in for OpenAICompatLLM: returns scripted replies in order."""

    def __init__(self, replies: list[LLMReply]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMReply:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self._replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self._replies.pop(0)
```

Also create empty `tests/__init__.py` so `from tests.helpers import ...` resolves.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_events.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/events.py tests/helpers.py tests/__init__.py tests/test_events.py
git commit -m "feat: core event contract and FakeLLM test double"
```

---

### Task 5: Repo tools — read_file, grep, glob (sandboxed to repo root)

**Files:**
- Create: `pyrrhon/core/tools/__init__.py` (empty), `pyrrhon/core/tools/base.py`, `pyrrhon/core/tools/repo.py`
- Create fixtures: `tests/fixtures/sample_repo/app.py`, `tests/fixtures/sample_repo/utils/helpers.py`, `tests/fixtures/sample_repo/README.md`
- Test: `tests/test_repo_tools.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Tool` ABC in `base.py`: class attrs `name: str`, `description: str`, `parameters: dict` (JSON schema); `async run(**kwargs) -> str`; concrete `schema() -> dict` returning `{"type": "function", "function": {"name", "description", "parameters"}}`
  - `ReadFileTool(root: Path)`, `GrepTool(root: Path)`, `GlobTool(root: Path)` in `repo.py` — all return strings; errors are `"ERROR: ..."` strings, never exceptions
  - `read_file` output format per line: `f"{n:>5}| {content}"` (1-based `n`) so the model can cite `path:line`

- [ ] **Step 1: Create the fixture repo**

`tests/fixtures/sample_repo/app.py`:

```python
from utils.helpers import greet


def main():
    print(greet("world"))


if __name__ == "__main__":
    main()
```

`tests/fixtures/sample_repo/utils/helpers.py`:

```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

`tests/fixtures/sample_repo/README.md`:

```markdown
# Sample

A tiny fixture app used by Pyrrhon's tests.
```

- [ ] **Step 2: Write the failing test**

`tests/test_repo_tools.py`:

```python
from pathlib import Path

from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_read_file_returns_numbered_lines():
    out = await ReadFileTool(FIXTURE).run(path="utils/helpers.py")
    assert "    1| def greet(name: str) -> str:" in out


async def test_read_file_rejects_escape_and_missing():
    tool = ReadFileTool(FIXTURE)
    assert (await tool.run(path="../outside.txt")).startswith("ERROR:")
    assert (await tool.run(path="nope.py")).startswith("ERROR:")


async def test_grep_reports_posix_path_line_and_text():
    out = await GrepTool(FIXTURE).run(pattern=r"def greet")
    assert "utils/helpers.py:1: def greet(name: str) -> str:" in out


async def test_grep_bad_regex_is_an_error_string():
    assert (await GrepTool(FIXTURE).run(pattern="(unclosed")).startswith("ERROR:")


async def test_glob_lists_matching_files():
    out = await GlobTool(FIXTURE).run(pattern="**/*.py")
    assert "app.py" in out and "utils/helpers.py" in out
    assert "README.md" not in out


async def test_tool_schema_shape():
    schema = ReadFileTool(FIXTURE).schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert "path" in schema["function"]["parameters"]["properties"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_repo_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.tools'`

- [ ] **Step 4: Write minimal implementation**

`pyrrhon/core/tools/base.py`:

```python
"""Tool ABC: every agent capability (built-in or MCP-bridged later) exposes this shape."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Tool(ABC):
    name: str
    description: str
    parameters: dict  # JSON schema for the arguments object

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """Execute and return text for the LLM. Failures return 'ERROR: ...' strings."""

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

`pyrrhon/core/tools/repo.py`:

```python
"""Read-only repo tools, sandboxed to the repo root.

Real-time discipline: `run()` methods do no filesystem work on the event
loop — the sync body is offloaded via asyncio.to_thread(), because in M3 a
~100ms loop stall becomes an audible audio glitch.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
from pathlib import Path

from pyrrhon.core.tools.base import Tool

SKIP_DIRS = {".git", ".pyrrhon", ".venv", "node_modules", "__pycache__"}
MAX_GREP_MATCHES = 50
MAX_GLOB_MATCHES = 100
MAX_READ_LINES = 400


def _resolve_inside(root: Path, rel: str) -> Path | None:
    """Resolve `rel` against root; None if it escapes the repo (e.g. '../')."""
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file from the repo. Returns numbered lines so claims can be "
        "cited as path:line."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path"},
            "start_line": {"type": "integer", "description": "1-based first line"},
            "end_line": {"type": "integer", "description": "1-based last line, inclusive"},
        },
        "required": ["path"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        return await asyncio.to_thread(self._read, path, start_line, end_line)

    def _read(self, path: str, start_line: int, end_line: int | None) -> str:
        target = _resolve_inside(self.root, path)
        if target is None:
            return f"ERROR: '{path}' is outside the repo."
        if not target.is_file():
            return f"ERROR: '{path}' does not exist."
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(start_line, 1)
        last = min(end_line or len(lines), first - 1 + MAX_READ_LINES, len(lines))
        numbered = [f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1)]
        return "\n".join(numbered) or f"(no lines in range for {path})"


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a Python regex. Returns 'path:line: text'."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression"},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, pattern: str) -> str:
        return await asyncio.to_thread(self._search, pattern)

    def _search(self, pattern: str) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"ERROR: bad regex: {exc}"
        hits: list[str] = []
        for path in _iter_files(self.root):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable — skip
            rel = path.relative_to(self.root).as_posix()
            for n, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()}")
                    if len(hits) >= MAX_GREP_MATCHES:
                        return "\n".join(hits) + "\n(truncated)"
        return "\n".join(hits) or "No matches."


class GlobTool(Tool):
    name = "glob"
    description = "List repo files matching a glob pattern like '**/*.py'."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'"},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, pattern: str) -> str:
        return await asyncio.to_thread(self._match, pattern)

    def _match(self, pattern: str) -> str:
        matches = [
            p.relative_to(self.root).as_posix()
            for p in _iter_files(self.root)
            if fnmatch.fnmatch(p.relative_to(self.root).as_posix(), pattern)
        ]
        return "\n".join(matches[:MAX_GLOB_MATCHES]) or "No files match."
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_repo_tools.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/tools tests/fixtures tests/test_repo_tools.py
git commit -m "feat: sandboxed repo tools (read_file, grep, glob)"
```

---

### Task 6: Citation extraction

**Files:**
- Create: `pyrrhon/core/grounding/__init__.py` (empty), `pyrrhon/core/grounding/citations.py`
- Test: `tests/test_citations.py`

**Interfaces:**
- Consumes: `Citation` from Task 4.
- Produces: `extract_citations(text: str, root: Path) -> list[Citation]` — finds `path:line` patterns, normalizes `\` to `/`, keeps only paths that exist under `root`, dedupes preserving order. (M1's grounding *gate* will build on this same function.)

- [ ] **Step 1: Write the failing test**

`tests/test_citations.py`:

```python
from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_citations

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def test_extracts_existing_file_citation():
    text = "Greeting lives at utils/helpers.py:1 and is called from app.py:5."
    cites = extract_citations(text, FIXTURE)
    assert Citation(file="utils/helpers.py", line=1) in cites
    assert Citation(file="app.py", line=5) in cites


def test_skips_paths_that_do_not_exist():
    cites = extract_citations("see made/up/file.py:12", FIXTURE)
    assert cites == []


def test_dedupes_and_normalizes_backslashes():
    text = r"utils\helpers.py:1 and again utils/helpers.py:1"
    cites = extract_citations(text, FIXTURE)
    assert cites == [Citation(file="utils/helpers.py", line=1)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_citations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.grounding'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/grounding/citations.py`:

```python
"""Find file:line references in agent prose. M1 turns this into a verification gate."""

from __future__ import annotations

import re
from pathlib import Path

from pyrrhon.core.events import Citation

_CITATION_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_][\w./\\-]*\.[A-Za-z0-9_]+):(?P<line>\d+)"
)


def extract_citations(text: str, root: Path) -> list[Citation]:
    seen: set[tuple[str, int]] = set()
    citations: list[Citation] = []
    for match in _CITATION_RE.finditer(text):
        rel = match.group("path").replace("\\", "/")
        line = int(match.group("line"))
        if not (root / rel).is_file():
            continue  # only surface citations that point at real files
        if (rel, line) in seen:
            continue
        seen.add((rel, line))
        citations.append(Citation(file=rel, line=line))
    return citations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_citations.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/grounding tests/test_citations.py
git commit -m "feat: extract file:line citations from agent prose"
```

---

### Task 7: Teaching prompt + soul files

**Files:**
- Create: `pyrrhon/core/agent/__init__.py` (empty), `pyrrhon/core/agent/prompts.py`, `pyrrhon/core/agent/soul.py`
- Test: `tests/test_soul.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `prompts.SYSTEM_PROMPT: str`
  - `soul.load_soul(repo_root: Path, home: Path | None = None) -> str` — concatenates every `*.md` in `<home>/.pyrrhon/` then `<repo>/.pyrrhon/` (sorted by filename within each dir), each prefixed `## From <filename>`; empty string if none
  - `soul.build_system_prompt(repo_root: Path, home: Path | None = None) -> str` — `SYSTEM_PROMPT` + repo-root line + soul section (if any)

- [ ] **Step 1: Write the failing test**

`tests/test_soul.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.soul import build_system_prompt, load_soul


def test_no_soul_files_yields_empty(tmp_path: Path):
    assert load_soul(repo_root=tmp_path, home=tmp_path / "nohome") == ""


def test_repo_soul_is_loaded_after_global(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".pyrrhon").mkdir(parents=True)
    (home / ".pyrrhon" / "soul.md").write_text("I am global.", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".pyrrhon").mkdir(parents=True)
    (repo / ".pyrrhon" / "soul.md").write_text("I am repo-level.", encoding="utf-8")
    (repo / ".pyrrhon" / "skill.md").write_text("Custom skill notes.", encoding="utf-8")

    soul = load_soul(repo_root=repo, home=home)
    assert soul.index("I am global.") < soul.index("I am repo-level.")
    assert "Custom skill notes." in soul
    assert "## From soul.md" in soul


def test_build_system_prompt_mentions_repo_and_soul(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".pyrrhon").mkdir(parents=True)
    (repo / ".pyrrhon" / "soul.md").write_text("Prefers first principles.", encoding="utf-8")
    prompt = build_system_prompt(repo_root=repo, home=tmp_path / "nohome")
    assert str(repo) in prompt
    assert "Prefers first principles." in prompt
    assert "path:line" in prompt  # citation rule present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_soul.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.agent'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/agent/prompts.py`:

```python
"""Pyrrhon's teaching policy. This is the product's personality — edit deliberately."""

SYSTEM_PROMPT = """\
You are Pyrrhon, a senior engineer sitting next to the user, discussing their
codebase out loud. Named for Pyrrho the skeptic: suspend judgment, question
assumptions, never bluff.

How you talk:
- Conversational, like pair programming — short turns, not lectures.
- Explain from first principles: what problem exists, why this construct
  solves it, what the alternatives were, and the trade-off that was chosen.
- Connect cause and effect across files: why a thing is done *here* and what
  it affects *there*.
- Point out where the code falls short of solid architecture or engineering
  standards, and how you would improve it.
- Ask one short check-question when it helps the user learn.

Hard rules:
- Every claim about the code cites a real location as path:line
  (example: utils/helpers.py:12). Use your tools to look before you cite.
- If you cannot verify something, say "I'm not certain" — never invent a
  path, symbol, or behavior. An honest gap beats a confident guess.
- Prefer citing a few exact lines over quoting long blocks.
"""
```

`pyrrhon/core/agent/soul.py`:

```python
"""Soul files: user-authored markdown loaded into the system prompt each session.

Users create them with /init (or by hand) in ~/.pyrrhon/ and <repo>/.pyrrhon/.
Global loads first, repo last — so repo-level context wins.
"""

from __future__ import annotations

from pathlib import Path

from pyrrhon.core.agent.prompts import SYSTEM_PROMPT


def load_soul(repo_root: Path, home: Path | None = None) -> str:
    home = home or Path.home()
    sections: list[str] = []
    for directory in (home / ".pyrrhon", repo_root / ".pyrrhon"):
        if not directory.is_dir():
            continue
        for md in sorted(directory.glob("*.md")):
            content = md.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"## From {md.name}\n\n{content}")
    return "\n\n".join(sections)


def build_system_prompt(repo_root: Path, home: Path | None = None) -> str:
    prompt = SYSTEM_PROMPT + f"\nThe repo under discussion is rooted at: {repo_root}\n"
    soul = load_soul(repo_root, home)
    if soul:
        prompt += f"\n# User context (soul files)\n\n{soul}\n"
    return prompt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_soul.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/agent tests/test_soul.py
git commit -m "feat: teaching system prompt and soul file loading"
```

---

### Task 8: Agent loop — tool calling → event stream

**Files:**
- Create: `pyrrhon/core/agent/loop.py`
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `Tool` (Task 5), events (Task 4), `LLMReply`/`ToolCall` (Task 3), `extract_citations` (Task 6), `FakeLLM` (Task 4, tests only).
- Produces:
  - `class Agent`: `__init__(llm, tools: list[Tool], system_prompt: str, repo_root: Path, max_tool_rounds: int = 8)`; `async run_turn(history: list[dict], user_text: str) -> AsyncIterator[Event]`
  - Contract: mutates `history` in place (caller owns conversation state); injects the system message on first turn; yields `ToolCallStarted`/`ToolCallFinished` around each tool, then `SpeechChunk` for the final text, then one `Citation` per verified-existing `path:line` in that text.
  - `llm` is anything with `async chat(messages, tools=None) -> LLMReply` (duck-typed; `FakeLLM` qualifies).

- [ ] **Step 1: Write the failing test**

`tests/test_agent_loop.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation, SpeechChunk, ToolCallFinished, ToolCallStarted
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_agent(replies, max_tool_rounds: int = 8) -> tuple[Agent, FakeLLM]:
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
        max_tool_rounds=max_tool_rounds,
    )
    return agent, fake


async def collect(agent: Agent, history: list[dict], text: str) -> list:
    return [event async for event in agent.run_turn(history, text)]


async def test_direct_answer_yields_speech_and_updates_history():
    agent, fake = make_agent([LLMReply(text="It prints a greeting.")])
    history: list[dict] = []
    events = await collect(agent, history, "what does app.py do?")
    assert events == [SpeechChunk(text="It prints a greeting.")]
    roles = [m["role"] for m in history]
    assert roles == ["system", "user", "assistant"]


async def test_tool_round_then_answer_with_citation():
    replies = [
        LLMReply(
            tool_calls=(
                ToolCall(id="call_1", name="read_file", arguments={"path": "utils/helpers.py"}),
            )
        ),
        LLMReply(text="greet is defined at utils/helpers.py:1."),
    ]
    agent, fake = make_agent(replies)
    events = await collect(agent, [], "where is greet defined?")

    assert ToolCallStarted(name="read_file", args={"path": "utils/helpers.py"}) in events
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert "def greet" in finished[0].result_preview
    assert Citation(file="utils/helpers.py", line=1) in events
    # The tool result was fed back to the LLM as a tool message:
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "call_1"


async def test_unknown_tool_reports_error_to_llm():
    replies = [
        LLMReply(tool_calls=(ToolCall(id="c1", name="nuke_repo", arguments={}),)),
        LLMReply(text="Sorry, I can't do that."),
    ]
    agent, fake = make_agent(replies)
    await collect(agent, [], "delete everything")
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["content"].startswith("ERROR:")


async def test_tool_budget_produces_honest_bailout():
    looping_call = LLMReply(
        tool_calls=(ToolCall(id="c", name="read_file", arguments={"path": "app.py"}),)
    )
    agent, _ = make_agent([looping_call, looping_call], max_tool_rounds=2)
    events = await collect(agent, [], "loop forever")
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert "tool budget" in speech[-1].text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_loop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.agent.loop'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/core/agent/loop.py`:

```python
"""The reasoning loop: LLM ⇄ tools, emitting the core event stream."""

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
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.base import Tool

PREVIEW_LEN = 200


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
    ):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        self.system_prompt = system_prompt
        self.repo_root = repo_root
        self.max_tool_rounds = max_tool_rounds

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
                history.append({"role": "assistant", "content": text})
                yield SpeechChunk(text=text)
                for citation in extract_citations(text, self.repo_root):
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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_loop.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/agent/loop.py tests/test_agent_loop.py
git commit -m "feat: agent tool-calling loop emitting the core event stream"
```

---

### Task 9: /init command, REPL channel, wire-up + docs

**Files:**
- Create: `pyrrhon/commands/__init__.py` (empty), `pyrrhon/commands/init_cmd.py`, `pyrrhon/repl.py`
- Modify: `CLAUDE.md` (record real commands)
- Test: `tests/test_init_and_repl.py`

**Interfaces:**
- Consumes: `load_settings`/`Settings` (Task 2), `create_llm`/`MissingAPIKeyError` (Task 3), events (Task 4), repo tools (Task 5), `build_system_prompt` (Task 7), `Agent` (Task 8).
- Produces:
  - `init_cmd.SOUL_TEMPLATE: str`; `init_cmd.init_pyrrhon_dir(repo_root: Path) -> tuple[Path, bool]` (path to `soul.md`, `True` if newly created; never overwrites)
  - `repl.build_agent(repo_root: Path, llm=None) -> Agent` (injectable `llm` for tests); `repl.run_repl(repo: str) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_init_and_repl.py`:

```python
from pathlib import Path

from pyrrhon.commands.init_cmd import init_pyrrhon_dir
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def test_init_creates_soul_template_once(tmp_path: Path):
    path, created = init_pyrrhon_dir(tmp_path)
    assert created is True
    assert path == tmp_path / ".pyrrhon" / "soul.md"
    assert "## Who I am" in path.read_text(encoding="utf-8")

    path.write_text("my edits", encoding="utf-8")
    _, created_again = init_pyrrhon_dir(tmp_path)
    assert created_again is False
    assert path.read_text(encoding="utf-8") == "my edits"  # never clobbered


async def test_build_agent_wires_tools_and_answers(tmp_path: Path):
    fake = FakeLLM([LLMReply(text="app.py:1 imports greet.")])
    agent = build_agent(FIXTURE, llm=fake)
    assert set(agent.tools) == {"read_file", "grep", "glob"}

    events = [event async for event in agent.run_turn([], "hi")]
    texts = [e.text for e in events if hasattr(e, "text")]
    assert "app.py:1 imports greet." in texts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_init_and_repl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.commands'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/commands/init_cmd.py`:

```python
"""/init — scaffold .pyrrhon/soul.md so users can tell Pyrrhon who they are."""

from __future__ import annotations

from pathlib import Path

SOUL_TEMPLATE = """\
# Soul

Tell Pyrrhon about yourself. Everything here is loaded into its context at
the start of every session in this repo. Add more .md files (e.g. skill.md)
next to this one — they load too.

## Who I am
<!-- role, experience level, languages you're comfortable in -->

## How I like things explained
<!-- e.g. first principles, short answers, always show the code -->

## Conventions and standards I care about
<!-- naming, architecture rules, style guides -->

## Current goals
<!-- what you're trying to learn or build right now -->
"""


def init_pyrrhon_dir(repo_root: Path) -> tuple[Path, bool]:
    directory = repo_root / ".pyrrhon"
    directory.mkdir(exist_ok=True)
    soul = directory / "soul.md"
    if soul.exists():
        return soul, False
    soul.write_text(SOUL_TEMPLATE, encoding="utf-8")
    return soul, True
```

`pyrrhon/repl.py`:

```python
"""Text REPL — the first (and thinnest) channel over the headless core."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from pyrrhon.commands.init_cmd import init_pyrrhon_dir
from pyrrhon.config.settings import load_settings
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.agent.soul import build_system_prompt
from pyrrhon.core.events import Citation, SpeechChunk, ToolCallStarted
from pyrrhon.core.providers.llm import MissingAPIKeyError, create_llm
from pyrrhon.core.tools.repo import GlobTool, GrepTool, ReadFileTool


def build_agent(repo_root: Path, llm=None) -> Agent:
    settings = load_settings(repo_root)
    llm = llm or create_llm(settings.fast, settings)
    tools = [ReadFileTool(repo_root), GrepTool(repo_root), GlobTool(repo_root)]
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=build_system_prompt(repo_root),
        repo_root=repo_root,
    )


def run_repl(repo: str) -> None:
    console = Console()
    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        console.print(f"[red]Not a directory: {repo_root}[/red]")
        raise SystemExit(1)
    try:
        agent = build_agent(repo_root)
    except MissingAPIKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    console.print(
        f"[bold]Pyrrhon[/bold] — discussing [cyan]{repo_root.name}[/cyan]. "
        "Commands: /init (personalize), /quit"
    )
    history: list[dict] = []
    while True:
        try:
            user = console.input("[bold cyan]you> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user in {"/quit", "/exit"}:
            break
        if user == "/init":
            path, created = init_pyrrhon_dir(repo_root)
            verb = "created" if created else "already exists"
            console.print(f"soul file {verb}: {path} — edit it, then restart the session.")
            continue
        asyncio.run(_turn(agent, history, user, console))


async def _turn(agent: Agent, history: list[dict], user: str, console: Console) -> None:
    async for event in agent.run_turn(history, user):
        if isinstance(event, ToolCallStarted):
            console.print(f"[dim]→ {event.name}({event.args})[/dim]")
        elif isinstance(event, SpeechChunk):
            console.print(Markdown(event.text))
        elif isinstance(event, Citation):
            console.print(f"[green]📍 {event.file}:{event.line}[/green]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_init_and_repl.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all 30 tests pass

- [ ] **Step 6: Manual smoke test (needs GROQ_API_KEY set)**

Run: `uv run pyrrhon .` — ask "what is this project?", confirm: tool-call lines appear dimmed, the answer renders as markdown, any `file:line` it mentions shows as a 📍 citation, `/init` creates `.pyrrhon/soul.md`, `/quit` exits. If you have no key handy, confirm instead that it exits with the `Set GROQ_API_KEY...` message.

- [ ] **Step 7: Record real commands in CLAUDE.md**

In `CLAUDE.md`, replace the paragraph:

```markdown
There is no test or lint setup yet. When adding one, prefer `pytest` run via
`uv run pytest` (single test: `uv run pytest path::test_name`), and record the
real commands here once they exist. Do not invent commands that aren't wired up.
```

with:

```markdown
- Run the app: `uv run pyrrhon [repo-path]` (needs `GROQ_API_KEY` set, or
  configure another provider in `.pyrrhon.toml`)
- Run tests: `uv run pytest` (single test: `uv run pytest path::test_name`)

There is no lint config yet. Current state: M0 (grounded text REPL) — see
`docs/superpowers/plans/2026-07-03-pyrrhon-m0-grounded-text-repl.md`.
```

- [ ] **Step 8: Commit**

```bash
git add pyrrhon/commands pyrrhon/repl.py tests/test_init_and_repl.py CLAUDE.md
git commit -m "feat: /init soul scaffolding and rich text REPL over the core"
```

---

## Definition of Done (M0)

- `uv run pytest` fully green.
- `uv run pyrrhon <some-repo>` answers questions about that repo in text, showing tool calls, markdown answers, and 📍 citations for real files.
- `/init` scaffolds `.pyrrhon/soul.md`; its content demonstrably changes the agent's behavior next session (it's in the system prompt).
- `core/` has no imports from `repl.py`/`commands/` (verify: `grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/` returns nothing).

## Next plans (written after M0 lands, one per milestone)

M1 grounding gate (split-path recovery) + eval + `remember` tool/memory.md → M2 Textual TUI → M3 Pipecat voice (barge-in, `TruncateSpeech`, turn cancellation) → M4 tree-sitter/git/web tools → M5 MCP + fallbacks → M6 design mode → M7 plugin loader.

Note for M4's plan: tree-sitter parsing and SQLite index writes are the heavy
CPU-bound cases the real-time discipline rule exists for — offload via
`asyncio.to_thread()`/`ProcessPoolExecutor` there, same as the M0 tools do.
