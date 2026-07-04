# Pyrrhon M5 — Extensibility: MCP Client, Provider Fallback Chains, Latency Polish

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Interface drift warning:** written before M0–M4 landed. Before executing, revalidate every Consumes signature against the actual codebase and update this plan if drifted. Re-verify the mcp SDK API against the installed version.

**Goal:** Any MCP server (stdio or streamable HTTP) declared in config contributes its tools to the agent automatically; a dead LLM provider triggers the configured fallback chain with a one-sentence notice instead of a dead session; and the status bar shows real per-turn latency (user text → first `SpeechChunk`) — the three extension seams and the latency discipline the spec requires of v1.

**Architecture:** `pyrrhon/core/mcp/` is a manager that owns MCP client sessions and bridges each remote tool into the existing `Tool` ABC — the agent loop never learns MCP exists, it just gets more tools. `FallbackLLM` wraps a chain of `OpenAICompatLLM` instances behind the same duck-typed `async chat(...)` the agent already uses, so fallback is invisible to `core/agent/`. Latency measurement lives in `Session` (core), display lives in the channels — the core/channel seam is unchanged.

**Tech Stack:** Python ≥ 3.12, uv, pydantic v2, openai SDK, `mcp` SDK (**pin `>=1.10,<2`** — see verified-API note below), pytest + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` — "Extension seams (v1)", "Error handling" (provider failure → fallback chain with one-sentence notice; MCP server crash → tools removed from roster, agent told), "Real-time discipline".

## Verified `mcp` SDK API (v1.x line, checked 2026-07-03 via Context7 `/modelcontextprotocol/python-sdk`)

- `from mcp import ClientSession, StdioServerParameters` and `from mcp.client.stdio import stdio_client`.
- `stdio_client(StdioServerParameters(command=..., args=[...]))` is an async context manager yielding `(read_stream, write_stream)`.
- `from mcp.client.streamable_http import streamablehttp_client`; `streamablehttp_client(url)` is an async context manager yielding a **3-tuple** `(read_stream, write_stream, get_session_id)`.
- `ClientSession(read_stream, write_stream)` (async context manager): `await session.initialize()`, `await session.list_tools()` → result with `.tools: list[mcp.types.Tool]` (each has `.name`, `.description`, `.inputSchema` JSON schema dict), `await session.call_tool(name, arguments)` → `CallToolResult` with `.content: list` (text blocks are `mcp.types.TextContent` with `.text`) and `.isError: bool`.
- Server side (test fixture): `from mcp.server.fastmcp import FastMCP`; `@mcp.tool()` decorator; `mcp.run(transport="stdio")`.
- **Why the `<2` pin:** the SDK's v2 migration renames `streamablehttp_client` → `streamable_http_client` (2-tuple, takes an `httpx.AsyncClient`), `FastMCP` → `MCPServer`, `inputSchema` → `input_schema`, and `isError` → `is_error`. This plan targets the v1 names; if v2 is what `uv add` installs at execution time, update Task 3's imports and field names accordingly.
- **anyio cancel-scope rule:** `stdio_client` / `streamablehttp_client` / `ClientSession` context managers must be entered and exited **in the same asyncio task**. `MCPManager.start()` and `.stop()` must therefore be awaited from the same task (one `asyncio.run(...)` / one Textual app loop), never from per-turn `asyncio.run()` calls.

## Global Constraints

(Copied verbatim from the M0 plan; still binding.)

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.**
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then.
- No grounding *verification* in M0 (that is M1); M0 only extracts citations for files that exist.
- Commit after every task (green tests only).

M5 additions:

- The "no grounding verification in M0" bullet above is historical — M1's grounding gate exists by now; MCP tool *results* flow through the same gate as built-in tool results, no special casing.
- MCP connection failures at startup are **never fatal**: a crashed/unreachable server contributes zero tools plus one logged warning line (`logging.getLogger("pyrrhon.mcp")`).
- Fallback chains replace the openai SDK's internal retries: chain members are constructed with `max_retries=0` (retrying a dead provider before falling back doubles worst-case latency for nothing).

## Assumed from M1–M4 (revalidate per the drift warning)

- Command registry: `pyrrhon/commands/registry.py` with decorator `command(name, help_text)`, `dispatch(line, ctx)`, and `CommandContext` dataclass carrying `repo_root`, `agent`, `ui`.
- `Session(agent)` in `pyrrhon/core/session.py` with an async-generator turn method `run_turn(user_text)` yielding `Event`s (wraps `Agent.run_turn` behind the M1 grounding gate).
- `Agent.__init__` grew kwargs `grounding_gate`, `allow_retry`, `deep_llm` (M1/M4) — keep them; this plan only *adds* to `build_agent`.
- `build_agent(repo_root, llm=None, ...)` is the single factory (originally in `pyrrhon/repl.py`; if M2 moved it, apply Task 4's edits wherever it lives now).

## File Structure (new/modified in M5)

```text
pyrrhon/
├── config/
│   └── settings.py          # MODIFIED: MCPServerConfig, mcp_servers, fallbacks
├── commands/
│   └── mcp_cmd.py           # NEW: /mcp list
├── core/
│   ├── mcp/
│   │   ├── __init__.py      # NEW: re-exports MCPManager, MCPToolAdapter
│   │   └── manager.py       # NEW: MCPManager, MCPToolAdapter
│   ├── providers/
│   │   └── llm.py           # MODIFIED: max_retries, FallbackLLM, create_llm_with_fallbacks
│   └── session.py           # MODIFIED: last_turn_latency_ms
└── repl.py                  # MODIFIED: build_agent(extra_tools=...), MCP + fallback wiring

tests/
├── fixtures/mcp_echo_server.py   # NEW: real FastMCP stdio server for tests
├── test_mcp_settings.py          # NEW
├── test_fallback_llm.py          # NEW
├── test_mcp_manager.py           # NEW
├── test_mcp_wiring.py            # NEW
└── test_latency.py               # NEW
```

---

### Task 1: Settings — MCP server tables and fallback chains

**Files:**
- Modify: `pyrrhon/config/settings.py`
- Test: `tests/test_mcp_settings.py`

**Interfaces:**
- Consumes: `Settings`, `load_settings` (M0 Task 2 — unchanged merge semantics).
- Produces:
  - `class MCPServerConfig(BaseModel)`: `command: str | None = None`, `args: list[str] = []`, `url: str | None = None`; a pydantic `model_validator(mode="after")` enforces **exactly one** of `command` / `url` set.
  - `Settings.mcp_servers: dict[str, MCPServerConfig] = {}` — loaded from `[mcp_servers.<name>]` TOML tables.
  - `Settings.fallbacks: dict[str, list[str]] = {}` — maps a slot name (`"fast"` / `"deep"`) to an **ordered provider list tried after the slot's primary**. Each entry is `"provider"` (reuses the slot's model) or `"provider/model"` (split on the *first* `/`, so `"openrouter/meta-llama/llama-3.3-70b"` → provider `openrouter`, model `meta-llama/llama-3.3-70b`).

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_settings.py`:

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrrhon.config.settings import MCPServerConfig, load_settings


def test_mcp_server_config_requires_exactly_one_transport():
    assert MCPServerConfig(command="npx", args=["-y", "some-server"]).command == "npx"
    assert MCPServerConfig(url="http://localhost:8931/mcp").url is not None
    with pytest.raises(ValidationError):
        MCPServerConfig()  # neither
    with pytest.raises(ValidationError):
        MCPServerConfig(command="npx", url="http://x")  # both


def test_mcp_servers_and_fallbacks_load_from_toml(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".pyrrhon.toml").write_text(
        "[mcp_servers.docs]\n"
        'command = "npx"\n'
        'args = ["-y", "@example/docs-mcp"]\n'
        "\n"
        "[mcp_servers.web]\n"
        'url = "http://localhost:8931/mcp"\n'
        "\n"
        "[fallbacks]\n"
        'fast = ["groq", "cerebras", "openai"]\n',
        encoding="utf-8",
    )
    settings = load_settings(repo_root=repo, home=tmp_path / "nohome")
    assert settings.mcp_servers["docs"].command == "npx"
    assert settings.mcp_servers["docs"].args == ["-y", "@example/docs-mcp"]
    assert settings.mcp_servers["web"].url == "http://localhost:8931/mcp"
    assert settings.fallbacks["fast"] == ["groq", "cerebras", "openai"]


def test_defaults_are_empty(tmp_path: Path):
    settings = load_settings(repo_root=tmp_path, home=tmp_path / "nohome")
    assert settings.mcp_servers == {}
    assert settings.fallbacks == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_settings.py -v`
Expected: FAIL with `ImportError: cannot import name 'MCPServerConfig' from 'pyrrhon.config.settings'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/config/settings.py`, change the pydantic import line to:

```python
from pydantic import BaseModel, model_validator
```

Add after `ProviderConfig` (before `BUILTIN_PROVIDERS`):

```python
class MCPServerConfig(BaseModel):
    """One [mcp_servers.<name>] table: a stdio command OR a streamable-HTTP url."""

    command: str | None = None
    args: list[str] = []
    url: str | None = None

    @model_validator(mode="after")
    def _exactly_one_transport(self) -> "MCPServerConfig":
        if (self.command is None) == (self.url is None):
            raise ValueError(
                "an MCP server needs exactly one of 'command' (stdio) or "
                "'url' (streamable HTTP)"
            )
        return self
```

Add two fields to `Settings` (after `providers: dict[str, ProviderConfig] = {}`):

```python
    mcp_servers: dict[str, MCPServerConfig] = {}
    # Slot name ("fast"/"deep") -> providers tried IN ORDER after the slot's
    # primary. Entry format: "provider" or "provider/model" (first '/' splits).
    fallbacks: dict[str, list[str]] = {}
```

No change to `load_settings` — the existing shallow TOML merge already feeds `mcp_servers` / `fallbacks` top-level tables into `Settings.model_validate`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_settings.py tests/test_settings.py -v`
Expected: 3 new tests + all existing settings tests pass

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/config/settings.py tests/test_mcp_settings.py
git commit -m "feat: MCP server tables and fallback chains in settings"
```

---

### Task 2: FallbackLLM and create_llm_with_fallbacks

**Files:**
- Modify: `pyrrhon/core/providers/llm.py`
- Test: `tests/test_fallback_llm.py`

**Interfaces:**
- Consumes: `OpenAICompatLLM`, `LLMReply`, `create_llm`, `MissingAPIKeyError` (M0 Task 3); `ModelSlot`, `Settings` (+ Task 1's `fallbacks`).
- Produces:
  - `OpenAICompatLLM.__init__(model: str, api_key: str, base_url: str | None = None, max_retries: int = 2)` — additive kwarg, default matches the openai SDK default so M0 behavior is unchanged.
  - `create_llm(slot: ModelSlot, settings: Settings, max_retries: int = 2) -> OpenAICompatLLM` — additive kwarg, passed through.
  - `class FallbackLLM`: `__init__(self, chain: list[OpenAICompatLLM], on_switch=None)`; public attrs `chain`, `on_switch` (settable after construction); property `model -> str` (active member's model, so status displays keep working); `async chat(messages, tools=None) -> LLMReply`. Semantics: tries chain members in order starting from the currently active one (sticky — a failed provider is not retried every turn; that is the latency polish); falls forward on `httpx.ConnectError`, `httpx.TimeoutException`, `openai.APIConnectionError` (the SDK wraps httpx transport errors in this), and `openai.APIStatusError` with `status_code >= 500`; 4xx re-raises immediately (a bad API key is not a provider outage); on each switch calls `on_switch(provider_index)` before trying that provider, so the channel can speak the one-sentence notice; all members exhausted → re-raise the last error.
  - `create_llm_with_fallbacks(slot_name: str, settings: Settings) -> FallbackLLM | OpenAICompatLLM` — `slot_name` is `"fast"` or `"deep"` (else `KeyError`). No fallbacks configured for the slot → plain `create_llm(slot, settings)` (M0 behavior). Otherwise the chain is `[primary] + fallback entries`, every member built with `max_retries=0`; a fallback entry whose API key env var is unset is **skipped with a logged warning** (only the primary's missing key still raises `MissingAPIKeyError`).

- [ ] **Step 1: Write the failing test**

`tests/test_fallback_llm.py`:

```python
import httpx
import pytest
import respx
from openai import APIStatusError

from pyrrhon.config.settings import Settings
from pyrrhon.core.providers.llm import (
    FallbackLLM,
    OpenAICompatLLM,
    create_llm_with_fallbacks,
)

B1 = "https://primary.example/v1"
B2 = "https://backup.example/v1"


def _completion(text: str) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
    }


def make_chain() -> FallbackLLM:
    return FallbackLLM(
        chain=[
            OpenAICompatLLM(model="m1", api_key="k", base_url=B1, max_retries=0),
            OpenAICompatLLM(model="m2", api_key="k", base_url=B2, max_retries=0),
        ]
    )


MESSAGES = [{"role": "user", "content": "hi"}]


@respx.mock
async def test_connect_error_falls_over_and_notifies():
    respx.post(f"{B1}/chat/completions").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("from backup"))
    )
    fb = make_chain()
    switches: list[int] = []
    fb.on_switch = switches.append
    reply = await fb.chat(MESSAGES)
    assert reply.text == "from backup"
    assert switches == [1]
    assert fb.model == "m2"  # active member is now the backup


@respx.mock
async def test_5xx_falls_over_but_is_sticky_next_turn():
    r1 = respx.post(f"{B1}/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("ok"))
    )
    fb = make_chain()
    assert (await fb.chat(MESSAGES)).text == "ok"
    assert (await fb.chat(MESSAGES)).text == "ok"  # second turn skips the dead primary
    assert r1.call_count == 1


@respx.mock
async def test_4xx_does_not_fall_over():
    respx.post(f"{B1}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    r2 = respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("never"))
    )
    with pytest.raises(APIStatusError):
        await make_chain().chat(MESSAGES)
    assert r2.call_count == 0


@respx.mock
async def test_all_exhausted_reraises_last():
    respx.post(f"{B1}/chat/completions").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": {"message": "overloaded"}})
    )
    with pytest.raises(APIStatusError) as excinfo:
        await make_chain().chat(MESSAGES)
    assert excinfo.value.status_code == 503


def test_factory_without_fallbacks_returns_plain_llm(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    llm = create_llm_with_fallbacks("fast", Settings())
    assert isinstance(llm, OpenAICompatLLM)


def test_factory_builds_chain_and_skips_missing_keys(monkeypatch, caplog):
    monkeypatch.setenv("GROQ_API_KEY", "sk-g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    settings = Settings(
        fallbacks={"fast": ["cerebras", "openrouter/meta-llama/llama-3.3-70b"]}
    )
    with caplog.at_level("WARNING", logger="pyrrhon.providers"):
        llm = create_llm_with_fallbacks("fast", settings)
    assert isinstance(llm, FallbackLLM)
    # primary (groq) + openrouter; cerebras skipped (no key)
    assert [m.model for m in llm.chain] == [
        "llama-3.3-70b-versatile",
        "meta-llama/llama-3.3-70b",
    ]
    assert "cerebras" in caplog.text


def test_factory_rejects_unknown_slot():
    with pytest.raises(KeyError):
        create_llm_with_fallbacks("medium", Settings())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fallback_llm.py -v`
Expected: FAIL with `ImportError: cannot import name 'FallbackLLM' from 'pyrrhon.core.providers.llm'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/providers/llm.py`, extend the imports:

```python
import logging

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI
```

Add below the imports:

```python
logger = logging.getLogger("pyrrhon.providers")
```

Change `OpenAICompatLLM.__init__` to:

```python
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_retries: int = 2,
    ):
        self.model = model
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=max_retries
        )
```

Change `create_llm` to:

```python
def create_llm(
    slot: ModelSlot, settings: Settings, max_retries: int = 2
) -> OpenAICompatLLM:
    provider = settings.provider_for(slot)
    api_key = os.environ.get(provider.api_key_env, "")
    if not api_key:
        raise MissingAPIKeyError(
            f"Set {provider.api_key_env} to use provider '{slot.provider}'."
        )
    return OpenAICompatLLM(
        model=slot.model,
        api_key=api_key,
        base_url=provider.base_url,
        max_retries=max_retries,
    )
```

Append to the end of the file:

```python
# Failures the fallback chain inspects. The openai SDK wraps httpx transport
# errors in APIConnectionError (APITimeoutError subclasses it); we catch the
# raw httpx layer too so a bare httpx error from a future adapter also falls
# over. APIStatusError is in the tuple, but chat() only falls over on 5xx —
# 4xx re-raises.
_FALLBACK_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    APIConnectionError,
    APIStatusError,
)


class FallbackLLM:
    """A chain of OpenAICompatLLMs behind the agent's duck-typed chat().

    Sticky: once a provider fails we stay on its successor for the rest of
    the session instead of paying a connect timeout on every turn (spec:
    "provider failure -> configured fallback chain with a one-sentence
    spoken notice"). 4xx errors re-raise immediately — a bad key is user
    error, not an outage.
    """

    def __init__(self, chain: list[OpenAICompatLLM], on_switch=None):
        if not chain:
            raise ValueError("FallbackLLM needs at least one provider in the chain")
        self.chain = list(chain)
        self.on_switch = on_switch  # callable(provider_index) | None
        self._active = 0

    @property
    def model(self) -> str:
        return self.chain[self._active].model

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        index = self._active
        while True:
            try:
                return await self.chain[index].chat(messages, tools=tools)
            except _FALLBACK_ERRORS as exc:
                if isinstance(exc, APIStatusError) and exc.status_code < 500:
                    raise  # 4xx: not a provider outage — never fall over
                if index + 1 >= len(self.chain):
                    raise  # chain exhausted: re-raise the last error
                index += 1
                self._active = index
                logger.warning(
                    "provider failed (%s); switching to '%s'",
                    type(exc).__name__,
                    self.chain[index].model,
                )
                if self.on_switch is not None:
                    self.on_switch(index)


def create_llm_with_fallbacks(
    slot_name: str, settings: Settings
) -> FallbackLLM | OpenAICompatLLM:
    """Build the LLM for a slot, honoring [fallbacks] from settings."""
    slots = {"fast": settings.fast, "deep": settings.deep_slot}
    if slot_name not in slots:
        raise KeyError(f"unknown model slot '{slot_name}' (expected 'fast' or 'deep')")
    slot = slots[slot_name]

    entries = settings.fallbacks.get(slot_name, [])
    if not entries:
        return create_llm(slot, settings)

    # The chain replaces the SDK's internal retries (max_retries=0): retrying
    # a dead provider before falling back doubles worst-case latency.
    chain = [create_llm(slot, settings, max_retries=0)]
    for entry in entries:
        provider, sep, model = entry.partition("/")
        entry_slot = ModelSlot(provider=provider, model=model if sep else slot.model)
        try:
            chain.append(create_llm(entry_slot, settings, max_retries=0))
        except MissingAPIKeyError as exc:
            logger.warning("skipping fallback provider '%s': %s", provider, exc)
    if len(chain) == 1:
        return chain[0]  # nothing usable behind the primary — behave like M0
    return FallbackLLM(chain)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fallback_llm.py tests/test_llm_adapter.py -v`
Expected: 8 new tests + all existing adapter tests pass

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/core/providers/llm.py tests/test_fallback_llm.py
git commit -m "feat: provider fallback chains with sticky switch and on_switch notice"
```

---

### Task 3: MCP manager + tool adapter, tested against a real stdio server

**Files:**
- Create: `pyrrhon/core/mcp/__init__.py`, `pyrrhon/core/mcp/manager.py`
- Create fixture: `tests/fixtures/mcp_echo_server.py`
- Test: `tests/test_mcp_manager.py`

**Interfaces:**
- Consumes: `Tool` ABC (M0 Task 5); `MCPServerConfig` (Task 1); `mcp` SDK (verified-API section above).
- Produces:
  - `class MCPToolAdapter(Tool)` — wraps one remote tool. `name = f"mcp_{server}_{tool}"` (both parts sanitized to `[A-Za-z0-9_-]` for the OpenAI tools API, prefix avoids collisions with built-ins and between servers); `description` from the remote tool (or a generated one); `parameters` = the remote `inputSchema` passed through unchanged. `run(**kwargs)` calls `session.call_tool`, concatenates text content blocks; a result with `isError` → `"ERROR: mcp server '<server>' failed: <text>"`; a protocol-level `McpError` → the same `ERROR:` string (server still alive); any other exception → the whole server is marked dead and every subsequent call to any of its adapters returns `"ERROR: mcp server '<server>' crashed earlier this session; its tools are unavailable."` — the spec's "tools removed from roster, agent told", implemented as the agent mechanically reading the unavailability on any attempted use.
  - `class MCPManager`: `__init__(self, configs: dict[str, MCPServerConfig])`; `async start(self) -> list[Tool]` connects every configured server (stdio via `stdio_client`, HTTP via `streamablehttp_client`) under a 10s per-server `asyncio.timeout`, populating `self.roster: dict[str, list[Tool]]` (failed server → `[]` + one `logging.getLogger("pyrrhon.mcp")` warning, **never an exception to the caller**); `async stop(self) -> None` closes all sessions/transports (must be awaited from the same task as `start()` — anyio cancel-scope rule).

- [ ] **Step 1: Add the dependency and create the fixture server**

Run:

```bash
uv add "mcp>=1.10,<2"
```

Expected: resolves and installs without error. (If resolution lands on a 2.x pre-release instead, stop and apply the v2 renames from the verified-API section.)

`tests/fixtures/mcp_echo_server.py`:

```python
"""A minimal real MCP server the tests launch over stdio."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input text back, prefixed."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 2: Write the failing test**

`tests/test_mcp_manager.py`:

```python
import sys
from pathlib import Path

from pyrrhon.config.settings import MCPServerConfig
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.mcp.manager import MCPToolAdapter, _ServerState

ECHO_SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def echo_config() -> MCPServerConfig:
    return MCPServerConfig(command=sys.executable, args=[str(ECHO_SERVER)])


async def test_start_exposes_prefixed_tools_and_call_roundtrips():
    manager = MCPManager({"echo": echo_config()})
    tools = await manager.start()
    try:
        assert [t.name for t in tools] == ["mcp_echo_echo"]
        assert len(manager.roster["echo"]) == 1
        schema = tools[0].schema()
        assert schema["function"]["name"] == "mcp_echo_echo"
        assert "text" in schema["function"]["parameters"]["properties"]
        assert await tools[0].run(text="hi") == "echo: hi"
    finally:
        await manager.stop()


async def test_unreachable_server_contributes_zero_tools(caplog):
    bad = MCPServerConfig(command="pyrrhon-no-such-binary-xyz", args=[])
    manager = MCPManager({"broken": bad, "echo": echo_config()})
    with caplog.at_level("WARNING", logger="pyrrhon.mcp"):
        tools = await manager.start()
    try:
        assert [t.name for t in tools] == ["mcp_echo_echo"]  # broken skipped
        assert manager.roster["broken"] == []
        assert "broken" in caplog.text
    finally:
        await manager.stop()


class _ExplodingSession:
    async def call_tool(self, name, arguments):
        raise RuntimeError("pipe closed")


class _RemoteTool:
    name = "echo"
    description = "d"
    inputSchema = {"type": "object", "properties": {}}


async def test_crashed_server_marks_all_its_tools_unavailable():
    state = _ServerState()
    adapter = MCPToolAdapter("flaky", _ExplodingSession(), _RemoteTool(), state)
    first = await adapter.run()
    assert first.startswith("ERROR: mcp server 'flaky' failed:")
    assert state.dead is True
    second = await adapter.run()
    assert "crashed earlier this session" in second
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.core.mcp'`

- [ ] **Step 4: Write minimal implementation**

`pyrrhon/core/mcp/manager.py`:

```python
"""MCP client manager: attach any MCP server's tools to the agent.

Spec seams honored here:
- extension seam #2: servers declared in config, tools exposed automatically;
- error handling: a crashed/unreachable server contributes zero tools plus a
  one-line warning at startup, and a mid-session crash makes every tool from
  that server answer with an ERROR string so the agent knows it's gone;
- anyio rule: the mcp SDK's transports pin cancel scopes to the entering
  task, so start() and stop() MUST be awaited from the same asyncio task.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from pyrrhon.config.settings import MCPServerConfig
from pyrrhon.core.tools.base import Tool

logger = logging.getLogger("pyrrhon.mcp")

CONNECT_TIMEOUT_S = 10.0


def _safe(name: str) -> str:
    """Sanitize for the OpenAI tools API name charset [A-Za-z0-9_-]."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


@dataclass
class _ServerState:
    """Shared by every adapter of one server: one crash kills the whole roster."""

    dead: bool = False


class MCPToolAdapter(Tool):
    """One remote MCP tool exposed through the ordinary Tool ABC."""

    def __init__(self, server_name: str, session, remote_tool, state: _ServerState):
        self.server_name = server_name
        self._session = session
        self._remote_name = remote_tool.name
        self._state = state
        self.name = f"mcp_{_safe(server_name)}_{_safe(remote_tool.name)}"
        self.description = remote_tool.description or (
            f"Tool '{remote_tool.name}' provided by MCP server '{server_name}'."
        )
        self.parameters = remote_tool.inputSchema or {
            "type": "object",
            "properties": {},
        }

    async def run(self, **kwargs) -> str:
        if self._state.dead:
            return (
                f"ERROR: mcp server '{self.server_name}' crashed earlier this "
                "session; its tools are unavailable."
            )
        try:
            result = await self._session.call_tool(self._remote_name, kwargs)
        except McpError as exc:
            # Protocol-level error: the request failed but the server lives.
            return f"ERROR: mcp server '{self.server_name}' failed: {exc}"
        except Exception as exc:
            # Transport-level failure: assume the server is gone for good.
            self._state.dead = True
            logger.warning("mcp server '%s' crashed: %s", self.server_name, exc)
            return f"ERROR: mcp server '{self.server_name}' failed: {exc}"
        texts = [
            block.text
            for block in result.content
            if isinstance(block, types.TextContent)
        ]
        text = "\n".join(texts).strip() or "(no text content)"
        if result.isError:
            return f"ERROR: mcp server '{self.server_name}' failed: {text}"
        return text


class MCPManager:
    """Owns the client sessions for every configured MCP server."""

    def __init__(self, configs: dict[str, MCPServerConfig]):
        self._configs = configs
        self._stacks: dict[str, AsyncExitStack] = {}
        self.roster: dict[str, list[Tool]] = {}

    async def start(self) -> list[Tool]:
        """Connect all configured servers; failures are logged, never raised."""
        tools: list[Tool] = []
        for name, cfg in self._configs.items():
            try:
                adapters = await self._connect(name, cfg)
            except Exception as exc:
                logger.warning(
                    "mcp server '%s' unavailable, contributing 0 tools: %s",
                    name,
                    exc,
                )
                self.roster[name] = []
                continue
            self.roster[name] = adapters
            tools.extend(adapters)
        return tools

    async def _connect(self, name: str, cfg: MCPServerConfig) -> list[Tool]:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                if cfg.command is not None:
                    params = StdioServerParameters(command=cfg.command, args=cfg.args)
                    read, write = await stack.enter_async_context(
                        stdio_client(params)
                    )
                else:
                    read, write, _get_session_id = await stack.enter_async_context(
                        streamablehttp_client(cfg.url)
                    )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listing = await session.list_tools()
        except BaseException:
            await stack.aclose()
            raise
        self._stacks[name] = stack
        state = _ServerState()
        return [
            MCPToolAdapter(name, session, remote_tool, state)
            for remote_tool in listing.tools
        ]

    async def stop(self) -> None:
        """Close every session. Must run in the same task that ran start()."""
        for name, stack in reversed(list(self._stacks.items())):
            try:
                await stack.aclose()
            except Exception as exc:
                logger.warning("error closing mcp server '%s': %s", name, exc)
        self._stacks.clear()
        self.roster.clear()
```

`pyrrhon/core/mcp/__init__.py`:

```python
from pyrrhon.core.mcp.manager import MCPManager, MCPToolAdapter

__all__ = ["MCPManager", "MCPToolAdapter"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_manager.py -v`
Expected: 3 passed (the first two spawn a real Python subprocess speaking MCP over stdio — allow a few seconds)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock pyrrhon/core/mcp tests/fixtures/mcp_echo_server.py tests/test_mcp_manager.py
git commit -m "feat: MCP client manager bridging remote tools into the Tool ABC"
```

---

### Task 4: Wiring — MCP tools into build_agent, /mcp list, fallback switch notice

**Files:**
- Modify: `pyrrhon/repl.py` (or wherever M1–M4 left `build_agent` and the channel startup — drift warning applies)
- Modify: `pyrrhon/commands/registry.py` (add `mcp` field to `CommandContext`)
- Create: `pyrrhon/commands/mcp_cmd.py`
- Test: `tests/test_mcp_wiring.py`

**Interfaces:**
- Consumes: `build_agent` (M0 Task 9, as extended by M1–M4), `command`/`CommandContext` registry (M2 — assumed shapes, revalidate), `MCPManager`/`MCPToolAdapter` (Task 3), `FallbackLLM`/`create_llm_with_fallbacks` (Task 2), `Settings.mcp_servers` (Task 1).
- Produces:
  - `build_agent(repo_root: Path, llm=None, extra_tools: list[Tool] | None = None, **existing_m1_m4_kwargs) -> Agent` — `extra_tools` are appended after the built-ins; default `llm` becomes `create_llm_with_fallbacks("fast", settings)` (drop-in: with no `[fallbacks]` configured it returns exactly what `create_llm(settings.fast, settings)` did).
  - `CommandContext.mcp: MCPManager | None = None` — additive field with default, so every existing construction site keeps working.
  - `mcp_cmd.render_mcp_roster(roster: dict[str, list[Tool]]) -> str` — pure renderer (testable without the registry); plus the registered `/mcp` command (`/mcp` or `/mcp list`) that renders `ctx.mcp.roster`.
  - Channel startup owns the MCP lifecycle: `manager = MCPManager(settings.mcp_servers)`; `mcp_tools = await manager.start()`; `build_agent(..., extra_tools=mcp_tools)`; `await manager.stop()` on exit — all in the app's single event loop, same task.

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_wiring.py`:

```python
import sys
from pathlib import Path

from pyrrhon.commands.mcp_cmd import render_mcp_roster
from pyrrhon.config.settings import MCPServerConfig
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"
ECHO_SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


async def test_build_agent_joins_mcp_tools_to_builtins():
    manager = MCPManager(
        {"echo": MCPServerConfig(command=sys.executable, args=[str(ECHO_SERVER)])}
    )
    tools = await manager.start()
    try:
        fake = FakeLLM([LLMReply(text="ok")])
        agent = build_agent(FIXTURE, llm=fake, extra_tools=tools)
        assert {"read_file", "grep", "glob"} <= set(agent.tools)
        assert "mcp_echo_echo" in agent.tools
    finally:
        await manager.stop()


def test_render_mcp_roster_shows_servers_counts_and_tools():
    class FakeTool:
        def __init__(self, name):
            self.name = name

    roster = {
        "echo": [FakeTool("mcp_echo_echo")],
        "broken": [],
    }
    out = render_mcp_roster(roster)
    assert "echo: 1 tool(s)" in out
    assert "  - mcp_echo_echo" in out
    assert "broken: unavailable (0 tools)" in out


def test_render_mcp_roster_empty():
    out = render_mcp_roster({})
    assert "No MCP servers configured" in out
    assert "[mcp_servers." in out  # tells the user how to add one
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_wiring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.commands.mcp_cmd'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/commands/mcp_cmd.py`:

```python
"""/mcp — show attached MCP servers and the tools they contribute."""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.core.tools.base import Tool


def render_mcp_roster(roster: dict[str, list[Tool]]) -> str:
    if not roster:
        return (
            "No MCP servers configured. Add an [mcp_servers.<name>] table to "
            ".pyrrhon.toml (command+args for stdio, or url for streamable HTTP)."
        )
    lines: list[str] = []
    for name, tools in roster.items():
        if tools:
            lines.append(f"{name}: {len(tools)} tool(s)")
            lines.extend(f"  - {tool.name}" for tool in tools)
        else:
            lines.append(f"{name}: unavailable (0 tools)")
    return "\n".join(lines)


@command("mcp", help_text="List attached MCP servers and their tools: /mcp list")
async def mcp_command(args: str, ctx: CommandContext) -> str:
    if args.strip() not in ("", "list"):
        return "Usage: /mcp list"
    roster = ctx.mcp.roster if ctx.mcp is not None else {}
    return render_mcp_roster(roster)
```

(Drift check: the M2 registry's decorator and handler signature are assumed as `command(name, help_text)` over `async def handler(args: str, ctx: CommandContext) -> str`; if M2 chose a different handler shape, keep `render_mcp_roster` exactly as written and adapt only the decorated wrapper. Also ensure `pyrrhon/commands/__init__.py` imports `mcp_cmd` the same way M2 registers its other command modules.)

In `pyrrhon/commands/registry.py`, add one field to `CommandContext` (with a default, so existing call sites are untouched):

```python
    mcp: "MCPManager | None" = None
```

with the import guarded to keep runtime deps thin:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrrhon.core.mcp import MCPManager
```

In `pyrrhon/repl.py` (or the current home of `build_agent`), update imports:

```python
from pyrrhon.core.providers.llm import (
    FallbackLLM,
    MissingAPIKeyError,
    create_llm_with_fallbacks,
)
from pyrrhon.core.tools.base import Tool
```

and change `build_agent` (keep any kwargs M1–M4 added; only the two marked lines change):

```python
def build_agent(
    repo_root: Path, llm=None, extra_tools: list[Tool] | None = None
) -> Agent:
    settings = load_settings(repo_root)
    llm = llm or create_llm_with_fallbacks("fast", settings)          # was create_llm(settings.fast, ...)
    tools = [
        ReadFileTool(repo_root),
        GrepTool(repo_root),
        GlobTool(repo_root),
        *(extra_tools or []),                                          # MCP adapters join here
    ]
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=build_system_prompt(repo_root),
        repo_root=repo_root,
    )
```

Finally, in the channel startup (the single-event-loop entry M2/M3 established — one `asyncio.run(...)` or the Textual app's mount; **not** a per-turn `asyncio.run`), wire the lifecycle and the one-sentence switch notice. Insert, adapted to the local variable names:

```python
settings = load_settings(repo_root)
manager = MCPManager(settings.mcp_servers)
mcp_tools = await manager.start()  # never raises; dead servers log one warning
try:
    agent = build_agent(repo_root, extra_tools=mcp_tools)
    if isinstance(agent.llm, FallbackLLM):
        llm = agent.llm
        # Spec: provider failure -> fallback chain "with a one-sentence
        # spoken notice". In text channels this prints; the M3 voice channel
        # points on_switch at its TTS queue instead.
        llm.on_switch = lambda i: ui.notice(
            f"My primary model stopped responding — switching to {llm.chain[i].model}."
        )
    ctx = CommandContext(repo_root=repo_root, agent=agent, ui=ui, mcp=manager)
    ...  # existing session/turn loop, unchanged
finally:
    await manager.stop()  # same task as start() — anyio cancel-scope rule
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_wiring.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: all tests pass (the `build_agent` default-LLM change is exercised by the existing M0 wiring test, which injects a FakeLLM and never hits the factory)

- [ ] **Step 6: Manual smoke test**

Add to the target repo's `.pyrrhon.toml`:

```toml
[mcp_servers.echo]
command = "python"
args = ["tests/fixtures/mcp_echo_server.py"]
```

Run `uv run pyrrhon .`, then `/mcp list` — expect `echo: 1 tool(s)` and `  - mcp_echo_echo`. Ask "use the echo tool on the word ping" — expect a tool-call line for `mcp_echo_echo` and an answer containing `echo: ping`. Remove the table afterwards.

- [ ] **Step 7: Commit**

```bash
git add pyrrhon/repl.py pyrrhon/commands tests/test_mcp_wiring.py
git commit -m "feat: wire MCP tools into the agent, /mcp list, fallback switch notice"
```

---

### Task 5: Latency polish — Session.last_turn_latency_ms + status display

**Files:**
- Modify: `pyrrhon/core/session.py`
- Modify: the M2 status bar (`pyrrhon/tui/`) — one-line display change, drift warning applies
- Test: `tests/test_latency.py`

**Interfaces:**
- Consumes: `Session(agent)` and its async-generator turn method (M2/M3 — assumed `run_turn(user_text)` yielding `Event`s; revalidate), `SpeechChunk` (M0 Task 4), `FakeLLM` (M0 Task 4).
- Produces: `Session.last_turn_latency_ms: float | None` — `None` until the first turn completes its first `SpeechChunk`; then the elapsed milliseconds from `run_turn(user_text)` entry to the moment the first `SpeechChunk` of that turn is yielded (this is the number the spec's status bar calls "live latency", and the metric the M3 voice budget is judged by). Measured with `time.monotonic()`; updated once per turn.

- [ ] **Step 1: Write the failing test**

`tests/test_latency.py`:

```python
from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import SpeechChunk
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.session import Session
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_session(replies) -> Session:
    agent = Agent(
        llm=FakeLLM(replies),
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
    )
    return Session(agent)


async def test_latency_none_before_first_turn_then_measured():
    session = make_session([LLMReply(text="It prints a greeting.")])
    assert session.last_turn_latency_ms is None
    events = [event async for event in session.run_turn("what does app.py do?")]
    assert any(isinstance(e, SpeechChunk) for e in events)
    assert isinstance(session.last_turn_latency_ms, float)
    assert session.last_turn_latency_ms >= 0.0


async def test_latency_updates_each_turn():
    session = make_session([LLMReply(text="one"), LLMReply(text="two")])
    async for _ in session.run_turn("first"):
        pass
    assert session.last_turn_latency_ms is not None
    session.last_turn_latency_ms = -1.0  # sentinel: prove the next turn re-measures
    async for _ in session.run_turn("second"):
        pass
    assert session.last_turn_latency_ms >= 0.0  # sentinel overwritten by a fresh value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_latency.py -v`
Expected: FAIL with `AttributeError: 'Session' object has no attribute 'last_turn_latency_ms'` (or `AssertionError` on the `None` check, depending on M2's `Session` — either way, red)

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/core/session.py`:

1. Add `import time` and ensure `SpeechChunk` is imported from `pyrrhon.core.events`.
2. In `Session.__init__`, add:

```python
        # Latency of the last turn: user_text -> first SpeechChunk, in ms.
        # Channels read this for the status bar; M3's voice budget is judged
        # against it. None until the first turn produces speech.
        self.last_turn_latency_ms: float | None = None
```

3. Rename the existing turn-driving async generator `run_turn` → `_run_turn_events` (its body — grounding gate, retry policy, everything from M1–M4 — is untouched), and add this new `run_turn` in its place:

```python
    async def run_turn(self, user_text: str) -> AsyncIterator[Event]:
        """Drive one turn, timing user_text -> first SpeechChunk."""
        started = time.monotonic()
        first_speech_seen = False
        async for event in self._run_turn_events(user_text):
            if not first_speech_seen and isinstance(event, SpeechChunk):
                self.last_turn_latency_ms = (time.monotonic() - started) * 1000.0
                first_speech_seen = True
            yield event
```

(`AsyncIterator` and `Event` are already imported by the M1–M4 `session.py`; add them if not.)

4. Status bar display (M2 TUI — drift warning applies to the widget's exact refresh method): where the status bar composes its text (mode + active models per the spec), append the latency segment:

```python
latency = self.session.last_turn_latency_ms
latency_text = f" | {latency:.0f} ms" if latency is not None else ""
```

and concatenate `latency_text` onto the existing status string, refreshing after each turn completes (the same place the status bar already refreshes the active-model display). If the plain REPL channel still exists alongside the TUI, print the equivalent after each turn:

```python
if session.last_turn_latency_ms is not None:
    console.print(f"[dim](first response in {session.last_turn_latency_ms:.0f} ms)[/dim]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_latency.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass (any M1–M4 test that called `session.run_turn` directly still passes — the wrapper yields the identical event stream)

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/core/session.py pyrrhon/tui tests/test_latency.py
git commit -m "feat: per-turn latency measurement exposed for the status bar"
```

---

## Definition of Done (M5)

- `uv run pytest` fully green, including the MCP manager tests that spawn a **real** stdio MCP server (`tests/fixtures/mcp_echo_server.py`) — no mocked MCP protocol anywhere.
- An `[mcp_servers.<name>]` table in `.pyrrhon.toml` (stdio `command`+`args`, or streamable-HTTP `url`) makes that server's tools callable by the agent as `mcp_<server>_<tool>`, visible via `/mcp list`; a misconfigured/dead server degrades to zero tools with one warning line, and the session still starts.
- An MCP server that dies mid-session never crashes a turn: every one of its tools answers with an `ERROR: mcp server '<name>' ...` string the agent can read and route around (spec: "tools removed from the roster; the agent is told they're unavailable").
- With `[fallbacks] fast = [...]` configured, killing the primary provider mid-session produces the one-sentence notice and a successful answer from the next provider in the chain; without `[fallbacks]`, behavior is byte-identical to M0's `create_llm`. A 4xx (bad key) still fails loudly instead of silently burning through the chain.
- The status bar (TUI) shows the last turn's user-text→first-`SpeechChunk` latency in ms, sourced from `Session.last_turn_latency_ms`; chain members run with `max_retries=0` and MCP connects are bounded by a 10s timeout, so no single dead dependency can stall a turn or startup indefinitely.
- `core/` still has no imports from channels (verify: `grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/` returns nothing — `pyrrhon/core/mcp/` included).
