# Pyrrhon M2 — Textual TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Interface drift warning:** written before M0/M1 landed. Before executing, revalidate every Consumes signature against the actual codebase and update this plan if drifted.

**Goal:** The full-screen Textual channel over the headless core: a transcript pane (left), a syntax-highlighted code viewer that auto-jumps to every citation (right), a status bar (mode + models), an input box — plus the decorator-registered slash-command registry (`/help`, `/init`, `/model`, `/code`) shared by the TUI and the M0 text REPL.

**Architecture:** `pyrrhon/tui/` and `pyrrhon/commands/registry.py` are thin subscribers to the same event stream the REPL consumes — `Agent.run_turn()` is untouched. Agent turns run inside a Textual **worker** so the UI event loop never blocks (the spec's real-time discipline: from M3 this same loop carries audio). The command registry is extension seam #3 from the spec; M7's plugin loader registers into the same table, and M3 (`/voice`), M5 (`/mcp`), M6 (`/mode`) add commands without touching dispatch. `repl.py`'s `build_agent` stays the single agent factory — the TUI reuses it.

**Tech Stack:** Python ≥ 3.12, uv, textual (8.x — 8.2.8 at time of writing, verified against current docs), rich (Syntax/Markdown renderables), pytest + pytest-asyncio (Textual pilot tests via `App.run_test()`).

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` — the "TUI (Textual)" section, the slash-command list, and "Real-time discipline" are binding.

**M1 is assumed landed and is consumed as-is, never rebuilt here:** `Agent.__init__` additionally accepts `grounding_gate: GroundingGate | None = None, allow_retry: bool = True`; `build_agent(repo_root, llm=None)` already wires the gate and registers the `remember` tool. Nothing in M2 constructs `Agent` directly except through `build_agent`.

## Global Constraints

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.**
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then.
- No grounding *verification* in M0 (that is M1); M0 only extracts citations for files that exist.
- Commit after every task (green tests only).

M2 additions:

- The grounding constraint above is historical: M1's gate exists and flows through `build_agent` unchanged — M2 renders its events, nothing more.
- **Agent turns in the TUI always run inside a Textual worker** (`self.run_worker(...)`), never awaited directly in a message handler — typing must stay responsive while the agent thinks.
- Slash-command handlers return **response strings** (errors prefixed `ERROR:`), never raise, and never print — the channel decides how to render them.
- `pyrrhon/tui/` and `pyrrhon/commands/` may import from `core/`; never the reverse.

## File Structure (locked in by this plan)

```text
pyrrhon/
├── cli.py                   # MODIFIED: --text keeps the M0 REPL; default launches the TUI
├── repl.py                  # MODIFIED: slash commands routed through dispatch(); ConsoleUI adapter
├── commands/
│   ├── __init__.py          # (M0, stays empty)
│   ├── init_cmd.py          # (M0) init_pyrrhon_dir — now wrapped by the /init command
│   ├── registry.py          # NEW: CommandContext, Command, @command, dispatch, /help
│   └── builtin.py           # NEW: /init, /model, /code (registered on import)
└── tui/
    ├── __init__.py          # NEW (empty)
    ├── widgets.py           # NEW: CodeViewer (rich Syntax pane), StatusBar
    └── app.py               # NEW: PyrrhonApp (transcript, viewer, status, input), run_tui

tests/
├── test_command_registry.py # NEW: registry unit tests (no UI, no Textual)
├── test_builtin_commands.py # NEW: /init, /model, /code via dispatch (no UI, no Textual)
├── test_tui_app.py          # NEW: pilot tests — layout, focus, show_citation
├── test_tui_turns.py        # NEW: pilot tests — FakeLLM-backed turns, slash dispatch
├── test_cli.py              # MODIFIED: --text → REPL, default → TUI
└── test_init_and_repl.py    # MODIFIED: ConsoleUI citation tracking
```

Interfaces pinned here that later milestones consume: `CommandContext` / `command` / `dispatch` (M3 `/voice`, M5 `/mcp`, M6 `/mode`, M7 plugin loader), `PyrrhonApp(repo_root, agent)` + `show_citation(citation)` (M3's voice bridge drives the same app), `--text` flag (M3 keeps it as the no-audio path).

---

### Task 1: Slash-command registry — `@command`, `dispatch`, `/help`

**Files:**
- Modify: `pyproject.toml` (via `uv add textual`)
- Create: `pyrrhon/commands/registry.py`
- Test: `tests/test_command_registry.py`

**Interfaces:**
- Consumes: `Agent` (M0 Task 8, type only), `build_agent(repo_root: Path, llm=None) -> Agent` from `pyrrhon.repl` (M0 Task 9, tests only), `FakeLLM` from `tests/helpers.py` (M0 Task 4, tests only).
- Produces:
  - `@dataclass CommandContext`: `repo_root: Path`, `agent: "Agent"`, `ui: object` (duck-typed: must offer `notify(text: str)`; may carry `last_citation`)
  - `@dataclass(frozen=True) Command`: `name: str`, `help_text: str`, `handler: Callable[[str, CommandContext], str]`
  - module-level `_COMMANDS: dict[str, Command]`
  - decorator `command(name: str, help_text: str)` — registers the decorated `(args: str, ctx: CommandContext) -> str` handler into `_COMMANDS`
  - `dispatch(line: str, ctx: CommandContext) -> str | None` — `None` if `line` is not a slash command (send it to the agent); the handler's response string if it is; an `"Unknown command '/xyz' — try /help."` string (not `None`) for unregistered `/xyz`
  - `/help` — registered in this module, lists every registered command

- [ ] **Step 1: Add the Textual dependency**

Run:

```bash
uv add textual
```

Expected: resolves textual 8.x (8.2.8 at time of writing) and `uv sync` state is clean. `pyproject.toml` gains `"textual"` in `[project] dependencies` with the version bound uv wrote.

- [ ] **Step 2: Write the failing test**

`tests/test_command_registry.py`:

```python
from pathlib import Path

from pyrrhon.commands.registry import CommandContext, command, dispatch
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class StubUI:
    """Minimal duck-typed ui for CommandContext — what registry tests need, nothing more."""

    def __init__(self):
        self.notes: list[str] = []
        self.last_citation = None

    def notify(self, text: str) -> None:
        self.notes.append(text)


def make_ctx() -> CommandContext:
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    return CommandContext(repo_root=FIXTURE, agent=agent, ui=StubUI())


@command("echo-test", "Echo the arguments back (test-only)")
def echo_test(args: str, ctx: CommandContext) -> str:
    return f"echo:{args}"


def test_plain_text_is_not_a_command():
    assert dispatch("what does app.py do?", make_ctx()) is None


def test_registered_command_receives_args():
    assert dispatch("/echo-test hello world", make_ctx()) == "echo:hello world"


def test_unknown_command_points_at_help():
    response = dispatch("/doesnotexist", make_ctx())
    assert response is not None
    assert "Unknown command" in response
    assert "/help" in response


def test_help_lists_registered_commands():
    response = dispatch("/help", make_ctx())
    assert "/help — List available commands" in response
    assert "/echo-test — Echo the arguments back (test-only)" in response
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_command_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.commands.registry'`

- [ ] **Step 4: Write minimal implementation**

`pyrrhon/commands/registry.py`:

```python
"""Decorator-registered slash commands — extension seam #3 from the spec.

Channel-agnostic: the text REPL and the TUI both call dispatch(); M3/M5/M6
add commands (/voice, /mcp, /mode) here, and M7's plugin loader registers
into the same table. Handlers return response strings (errors prefixed
'ERROR:'), never raise, and never print — the channel renders the string.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrrhon.core.agent.loop import Agent


@dataclass
class CommandContext:
    repo_root: Path
    agent: "Agent"
    ui: object  # duck-typed: needs notify(text: str); may carry last_citation


@dataclass(frozen=True)
class Command:
    name: str
    help_text: str
    handler: Callable[[str, CommandContext], str]


_COMMANDS: dict[str, Command] = {}


def command(name: str, help_text: str):
    """Register a slash command. Handler: (args, ctx) -> response string."""

    def register(fn: Callable[[str, CommandContext], str]):
        _COMMANDS[name] = Command(name=name, help_text=help_text, handler=fn)
        return fn

    return register


def dispatch(line: str, ctx: CommandContext) -> str | None:
    """Route `line` to a command. None means 'not a command — send to the agent'."""
    line = line.strip()
    if not line.startswith("/"):
        return None
    name, _, args = line[1:].partition(" ")
    cmd = _COMMANDS.get(name)
    if cmd is None:
        return f"Unknown command '/{name}' — try /help."
    return cmd.handler(args.strip(), ctx)


@command("help", "List available commands")
def help_command(args: str, ctx: CommandContext) -> str:
    return "\n".join(
        f"/{cmd.name} — {cmd.help_text}"
        for cmd in sorted(_COMMANDS.values(), key=lambda c: c.name)
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_command_registry.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock pyrrhon/commands/registry.py tests/test_command_registry.py
git commit -m "feat: decorator-registered slash-command registry with dispatch and /help"
```

---

### Task 2: Built-in commands — `/init`, `/model`, `/code`

**Files:**
- Create: `pyrrhon/commands/builtin.py`
- Test: `tests/test_builtin_commands.py`

**Interfaces:**
- Consumes: `command`, `CommandContext` (Task 1); `init_pyrrhon_dir(repo_root: Path) -> tuple[Path, bool]` (M0 Task 9); `ModelSlot`, `load_settings` (M0 Task 2); `create_llm(slot, settings)`, `MissingAPIKeyError` (M0 Task 3); `Citation` (M0 Task 4, tests only); `build_agent` (M0 Task 9, tests only).
- Produces:
  - `/init` — ports the REPL's inline handler: calls `init_pyrrhon_dir(ctx.repo_root)`, reports created vs already-exists
  - `/model <fast|deep> <provider>/<model>` — rebuilds an LLM via `create_llm`; `fast` replaces `ctx.agent.llm`; `deep` stores `ctx.agent.deep_llm` (the attribute M4's escalation will read); bad usage / unknown provider / missing key return `ERROR:` strings
  - `/code` — opens the most recent citation (`ctx.ui.last_citation`) in VS Code via `code --goto <abs-path>:<line>` using a non-blocking `subprocess.Popen`; no citation, missing `code` CLI, or launch failure return `ERROR:` strings
  - Registration happens at import: channels do `from pyrrhon.commands import builtin  # noqa: F401`

- [ ] **Step 1: Write the failing test**

`tests/test_builtin_commands.py`:

```python
from pathlib import Path

from pyrrhon.commands import builtin  # noqa: F401 — registers /init, /model, /code
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.events import Citation
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class StubUI:
    def __init__(self):
        self.notes: list[str] = []
        self.last_citation: Citation | None = None

    def notify(self, text: str) -> None:
        self.notes.append(text)


def make_ctx(repo_root: Path = FIXTURE) -> CommandContext:
    agent = build_agent(repo_root, llm=FakeLLM([]))
    return CommandContext(repo_root=repo_root, agent=agent, ui=StubUI())


def test_init_scaffolds_soul_via_dispatch(tmp_path: Path):
    response = dispatch("/init", make_ctx(tmp_path))
    assert "soul file created" in response
    assert (tmp_path / ".pyrrhon" / "soul.md").is_file()


def test_model_fast_swaps_agent_llm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ctx = make_ctx()
    old_llm = ctx.agent.llm
    response = dispatch("/model fast openai/gpt-4.1-mini", ctx)
    assert response == "fast slot is now openai/gpt-4.1-mini."
    assert ctx.agent.llm is not old_llm
    assert ctx.agent.llm.model == "gpt-4.1-mini"


def test_model_deep_stores_slot_for_m4(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    ctx = make_ctx()
    # provider is the first path segment; the model may itself contain slashes
    dispatch("/model deep openrouter/deepseek/deepseek-r1", ctx)
    assert ctx.agent.deep_llm.model == "deepseek/deepseek-r1"


def test_model_bad_usage_and_unknown_provider():
    ctx = make_ctx()
    assert dispatch("/model fast", ctx).startswith("ERROR: usage:")
    assert dispatch("/model warp openai/gpt-4.1-mini", ctx).startswith("ERROR: usage:")
    assert dispatch("/model fast doesnotexist/m", ctx).startswith("ERROR:")


def test_model_missing_key_is_error(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    ctx = make_ctx()
    response = dispatch("/model fast cerebras/llama3.3-70b", ctx)
    assert response.startswith("ERROR:")
    assert "CEREBRAS_API_KEY" in response


def test_code_without_citation_is_error():
    assert dispatch("/code", make_ctx()).startswith("ERROR: no citation")


def test_code_launches_vscode(monkeypatch):
    launched: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd):
            launched.append(cmd)

    monkeypatch.setattr("pyrrhon.commands.builtin.which", lambda _: "C:/fake/bin/code")
    monkeypatch.setattr("pyrrhon.commands.builtin.Popen", FakePopen)
    ctx = make_ctx()
    ctx.ui.last_citation = Citation(file="utils/helpers.py", line=1)
    response = dispatch("/code", ctx)
    assert response == "Opened utils/helpers.py:1 in VS Code."
    assert launched[0][0] == "C:/fake/bin/code"
    assert launched[0][1] == "--goto"
    assert launched[0][2].endswith("helpers.py:1")


def test_code_missing_cli_is_error(monkeypatch):
    monkeypatch.setattr("pyrrhon.commands.builtin.which", lambda _: None)
    ctx = make_ctx()
    ctx.ui.last_citation = Citation(file="app.py", line=1)
    assert dispatch("/code", ctx).startswith("ERROR: VS Code CLI")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_builtin_commands.py -v`
Expected: FAIL at collection with `ImportError: cannot import name 'builtin' from 'pyrrhon.commands'` (the module does not exist yet)

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/commands/builtin.py`:

```python
"""Built-in slash commands: /init, /model, /code.

Importing this module registers them (the @command decorator writes into
the registry table); channels do:

    from pyrrhon.commands import builtin  # noqa: F401
"""

from __future__ import annotations

from shutil import which
from subprocess import Popen

from pyrrhon.commands.init_cmd import init_pyrrhon_dir
from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.config.settings import ModelSlot, load_settings
from pyrrhon.core.providers.llm import MissingAPIKeyError, create_llm


@command("init", "Scaffold .pyrrhon/soul.md so Pyrrhon knows who you are")
def init_command(args: str, ctx: CommandContext) -> str:
    path, created = init_pyrrhon_dir(ctx.repo_root)
    verb = "created" if created else "already exists"
    return f"soul file {verb}: {path} — edit it, then restart the session."


@command("model", "Switch a model slot: /model <fast|deep> <provider>/<model>")
def model_command(args: str, ctx: CommandContext) -> str:
    usage = "ERROR: usage: /model <fast|deep> <provider>/<model>"
    parts = args.split()
    if len(parts) != 2 or "/" not in parts[1]:
        return usage
    slot_name, spec = parts
    if slot_name not in ("fast", "deep"):
        return usage
    # First path segment is the provider; the rest is the model (OpenRouter
    # model ids contain slashes, e.g. deepseek/deepseek-r1).
    provider, _, model = spec.partition("/")
    settings = load_settings(ctx.repo_root)
    try:
        llm = create_llm(ModelSlot(provider=provider, model=model), settings)
    except (KeyError, MissingAPIKeyError) as exc:
        return f"ERROR: {exc}"
    if slot_name == "fast":
        ctx.agent.llm = llm
        return f"fast slot is now {provider}/{model}."
    # Stored for M4's escalation logic; validated (provider + key) today so
    # the user finds out about a bad config now, not mid-question later.
    ctx.agent.deep_llm = llm
    return f"deep slot is now {provider}/{model} (escalation lands in M4)."


@command("code", "Open the current citation in VS Code")
def code_command(args: str, ctx: CommandContext) -> str:
    citation = getattr(ctx.ui, "last_citation", None)
    if citation is None:
        return "ERROR: no citation to open yet — ask about the code first."
    exe = which("code")
    if exe is None:
        return "ERROR: VS Code CLI ('code') not found on PATH."
    target = f"{ctx.repo_root / citation.file}:{citation.line or 1}"
    try:
        # Popen (not run): fire-and-forget, never blocks the channel's loop.
        Popen([exe, "--goto", target])
    except OSError as exc:
        return f"ERROR: could not launch VS Code: {exc}"
    return f"Opened {citation.file}:{citation.line or 1} in VS Code."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_builtin_commands.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/commands/builtin.py tests/test_builtin_commands.py
git commit -m "feat: built-in slash commands /init, /model, /code"
```

---

### Task 3: TUI skeleton — layout, CodeViewer, StatusBar, `show_citation`

**Files:**
- Create: `pyrrhon/tui/__init__.py` (empty), `pyrrhon/tui/widgets.py`, `pyrrhon/tui/app.py`
- Test: `tests/test_tui_app.py`

**Interfaces:**
- Consumes: `Agent` (M0 Task 8), `Citation` (M0 Task 4), `build_agent` + `FakeLLM` (tests only).
- Produces:
  - `widgets.CodeViewer(Static)`: `show(citation: Citation, root: Path) -> None` — loads the file, renders a rich `Syntax` window centered on the cited line (`highlight_lines={line}`, `line_numbers=True`); sets `current_file: str | None`, `current_line: int | None`; unreadable file → `ERROR:` text in the pane, no crash
  - `widgets.StatusBar(Static)`: `show_status(mode: str, fast_model: str, deep_model: str) -> None`; readable `status_text: str`
  - `app.PyrrhonApp(App)`: constructed `PyrrhonApp(repo_root: Path, agent: Agent)`; attrs `history: list[dict]`, `last_citation: Citation | None`, `last_command_response: str | None`; layout: `#transcript` `RichLog` (left) · `CodeViewer` (right) · `StatusBar` · `#prompt` `Input` (bottom, focused on mount); methods `show_citation(citation: Citation) -> None` (records `last_citation`, jumps the viewer) and `refresh_status() -> None`
  - `PyrrhonApp.notify(text)` already satisfies the registry's duck-typed `ui` (Textual's `App.notify(message, *, title="", severity="information", ...)` — verified against current docs), so the app itself is the `CommandContext.ui`

- [ ] **Step 1: Write the failing test**

`tests/test_tui_app.py`:

```python
from pathlib import Path

from textual.widgets import Input, RichLog

from pyrrhon.core.events import Citation
from pyrrhon.repl import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.widgets import CodeViewer, StatusBar
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_app() -> PyrrhonApp:
    agent = build_agent(FIXTURE, llm=FakeLLM([]))
    return PyrrhonApp(repo_root=FIXTURE, agent=agent)


async def test_layout_panes_status_and_focused_input():
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#transcript", RichLog) is not None
        assert app.query_one(CodeViewer) is not None
        assert app.query_one("#prompt", Input).has_focus
        assert "mode: understand" in app.query_one(StatusBar).status_text


async def test_show_citation_jumps_code_viewer():
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        app.show_citation(Citation(file="utils/helpers.py", line=1))
        await pilot.pause()
        viewer = app.query_one(CodeViewer)
        assert viewer.current_file == "utils/helpers.py"
        assert viewer.current_line == 1
        assert app.last_citation == Citation(file="utils/helpers.py", line=1)


async def test_show_citation_unreadable_file_is_error_not_crash():
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        app.show_citation(Citation(file="does/not/exist.py", line=3))
        await pilot.pause()
        viewer = app.query_one(CodeViewer)
        assert viewer.current_file is None  # nothing loaded, app still alive
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.tui'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/tui/widgets.py`:

```python
"""Custom widgets for the Pyrrhon TUI: the code viewer and the status bar.

Channel code — small sync file reads here are acceptable (the core/ hard
rule about asyncio.to_thread targets core/); M3 revisits if profiling says
otherwise.
"""

from __future__ import annotations

from pathlib import Path

from rich.syntax import Syntax
from textual.widgets import Static

from pyrrhon.core.events import Citation

CONTEXT_LINES = 15  # lines shown above and below the cited line


class CodeViewer(Static):
    """Right-hand pane: syntax-highlighted view of the most recent citation."""

    def __init__(self, **kwargs):
        super().__init__("No citation yet — ask about the code.", **kwargs)
        self.current_file: str | None = None
        self.current_line: int | None = None

    def show(self, citation: Citation, root: Path) -> None:
        path = root / citation.file
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.update(f"ERROR: could not read {citation.file}: {exc}")
            return
        line = citation.line or 1
        # "Centered": a symmetric window around the cited line. The pane's
        # height varies with the terminal, so a fixed window is the stable
        # approximation; the cited line itself is highlighted.
        window = (max(1, line - CONTEXT_LINES), line + CONTEXT_LINES)
        syntax = Syntax(
            source,
            lexer=Syntax.guess_lexer(str(path), code=source),
            line_numbers=True,
            line_range=window,
            highlight_lines={line},
        )
        self.current_file = citation.file
        self.current_line = line
        self.update(syntax)


class StatusBar(Static):
    """One-line status: mode plus the two model slots."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status_text: str = ""

    def show_status(self, mode: str, fast_model: str, deep_model: str) -> None:
        self.status_text = f"mode: {mode} · fast: {fast_model} · deep: {deep_model}"
        self.update(self.status_text)
```

`pyrrhon/tui/app.py`:

```python
"""The Textual TUI — the second channel over the headless core (M2).

Layout: transcript (left) · code viewer (right) · status bar · input.
Agent turns run in a Textual worker so this event loop never blocks —
from M3 the same loop carries audio (spec: real-time discipline).
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, RichLog

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation
from pyrrhon.tui.widgets import CodeViewer, StatusBar


class PyrrhonApp(App):
    TITLE = "Pyrrhon"

    CSS = """
    #panes {
        height: 1fr;
    }
    #transcript {
        width: 3fr;
        padding: 0 1;
    }
    CodeViewer {
        width: 2fr;
        border-left: solid $accent;
        padding: 0 1;
    }
    StatusBar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, repo_root: Path, agent: Agent):
        super().__init__()
        self.repo_root = repo_root
        self.agent = agent
        self.history: list[dict] = []
        self.last_citation: Citation | None = None
        self.last_command_response: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            yield RichLog(id="transcript", wrap=True, markup=True)
            yield CodeViewer(id="code-viewer")
        yield StatusBar(id="status-bar")
        yield Input(placeholder="Ask about the repo — or /help", id="prompt")

    def on_mount(self) -> None:
        self.refresh_status()
        self.query_one("#prompt", Input).focus()
        self.query_one("#transcript", RichLog).write(
            f"Pyrrhon — discussing {self.repo_root.name}. Type /help for commands."
        )

    def show_citation(self, citation: Citation) -> None:
        """Record the citation and jump the code viewer to it."""
        self.last_citation = citation
        self.query_one(CodeViewer).show(citation, self.repo_root)

    def refresh_status(self) -> None:
        fast = getattr(self.agent.llm, "model", "unknown")
        deep = getattr(getattr(self.agent, "deep_llm", None), "model", "= fast")
        self.query_one(StatusBar).show_status("understand", fast, deep)
```

Create empty `pyrrhon/tui/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tui_app.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/tui tests/test_tui_app.py
git commit -m "feat: Textual TUI skeleton with code viewer, status bar, citation jump"
```

---

### Task 4: Agent turns in a worker + slash dispatch + `run_tui`

**Files:**
- Modify: `pyrrhon/tui/app.py`
- Test: `tests/test_tui_turns.py`

**Interfaces:**
- Consumes: `Agent.run_turn(history: list[dict], user_text: str) -> AsyncIterator[Event]` (M0 Task 8); events `SpeechChunk`, `ScreenArtifact`, `Citation`, `ToolCallStarted` (M0 Task 4); `dispatch`, `CommandContext` (Task 1); builtin commands (Task 2); `build_agent` (M0 Task 9); `MissingAPIKeyError` (M0 Task 3); `LLMReply`/`ToolCall` + `FakeLLM` (tests only).
- Produces:
  - `Input.Submitted` handler: echoes the prompt, tries `dispatch(text, CommandContext(repo_root, agent, ui=self))` first — a command's response renders in the transcript (and is recorded on `last_command_response`) without touching the LLM or history; otherwise the turn runs via `self.run_worker(self._agent_turn(text), exclusive=True)` with the input disabled until the turn ends
  - Event rendering: `SpeechChunk` → transcript markdown; `ToolCallStarted` → dim line; `Citation` → green transcript chip + `show_citation` jump; `ScreenArtifact` → transcript markdown (M3 refines per-kind rendering)
  - `run_tui(repo: str) -> None` — resolves the path, builds the agent through `pyrrhon.repl.build_agent` (the single factory), runs `PyrrhonApp`; missing directory or API key → message + `SystemExit(1)`

- [ ] **Step 1: Write the failing test**

`tests/test_tui_turns.py`:

```python
from pathlib import Path

from textual.widgets import Input

from pyrrhon.core.events import Citation
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.repl import build_agent
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.widgets import CodeViewer
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_app(replies, repo_root: Path = FIXTURE) -> tuple[PyrrhonApp, FakeLLM]:
    fake = FakeLLM(replies)
    agent = build_agent(repo_root, llm=fake)
    return PyrrhonApp(repo_root=repo_root, agent=agent), fake


async def submit(app: PyrrhonApp, pilot, text: str) -> None:
    app.query_one("#prompt", Input).value = text
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_turn_streams_speech_citation_and_code_jump():
    replies = [
        LLMReply(
            tool_calls=(
                ToolCall(id="c1", name="read_file", arguments={"path": "utils/helpers.py"}),
            )
        ),
        LLMReply(text="greet is defined at utils/helpers.py:1."),
    ]
    app, fake = make_app(replies)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "where is greet defined?")
        assert app.history[-1] == {
            "role": "assistant",
            "content": "greet is defined at utils/helpers.py:1.",
        }
        assert app.last_citation == Citation(file="utils/helpers.py", line=1)
        assert app.query_one(CodeViewer).current_line == 1
        prompt = app.query_one("#prompt", Input)
        assert not prompt.disabled and prompt.has_focus  # ready for the next turn


async def test_slash_command_short_circuits_the_agent():
    app, fake = make_app([])  # any LLM call would raise inside FakeLLM
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/help")
        assert fake.calls == []  # the LLM was never touched
        assert app.history == []  # commands are not conversation
        assert "/model" in app.last_command_response


async def test_unknown_command_is_reported():
    app, fake = make_app([])
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/definitely-not-a-command")
        assert "Unknown command" in app.last_command_response


async def test_init_via_tui(tmp_path: Path):
    app, fake = make_app([], repo_root=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/init")
        assert (tmp_path / ".pyrrhon" / "soul.md").is_file()
        assert "soul file created" in app.last_command_response
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_turns.py -v`
Expected: 4 failed — the app mounts but has no `Input.Submitted` handler, so nothing happens on enter: assertions on `history`/`last_citation` fail, and `"..." in app.last_command_response` raises `TypeError: argument of type 'NoneType' is not iterable`

- [ ] **Step 3: Write the implementation**

Replace the import block at the top of `pyrrhon/tui/app.py` with exactly:

```python
from __future__ import annotations

from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, RichLog

from pyrrhon.commands import builtin  # noqa: F401 — registers /init, /model, /code
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import (
    Citation,
    ScreenArtifact,
    SpeechChunk,
    ToolCallStarted,
)
from pyrrhon.core.providers.llm import MissingAPIKeyError
from pyrrhon.tui.widgets import CodeViewer, StatusBar
```

Add these two methods to `PyrrhonApp`, directly below `refresh_status`:

```python
    @on(Input.Submitted, "#prompt")
    async def on_prompt_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        transcript = self.query_one("#transcript", RichLog)
        transcript.write(Text(f"you> {text}", style="bold cyan"))

        ctx = CommandContext(repo_root=self.repo_root, agent=self.agent, ui=self)
        response = dispatch(text, ctx)
        if response is not None:
            self.last_command_response = response
            style = "red" if response.startswith("ERROR") else "yellow"
            transcript.write(Text(response, style=style))
            self.refresh_status()  # /model may have swapped a slot
            return

        # One turn at a time; M3 replaces this with real barge-in/cancellation.
        event.input.disabled = True
        self.run_worker(self._agent_turn(text), exclusive=True)

    async def _agent_turn(self, user_text: str) -> None:
        """Consume the core event stream inside a worker — the UI never blocks."""
        transcript = self.query_one("#transcript", RichLog)
        prompt = self.query_one("#prompt", Input)
        try:
            async for event in self.agent.run_turn(self.history, user_text):
                if isinstance(event, SpeechChunk):
                    transcript.write(Markdown(event.text))
                elif isinstance(event, ToolCallStarted):
                    transcript.write(Text(f"→ {event.name}({event.args})", style="dim"))
                elif isinstance(event, Citation):
                    transcript.write(Text(f"📍 {event.file}:{event.line}", style="green"))
                    self.show_citation(event)
                elif isinstance(event, ScreenArtifact):
                    # M0/M1 never emit these; rendered plainly until M3 refines per-kind.
                    transcript.write(Markdown(event.content))
        finally:
            prompt.disabled = False
            prompt.focus()
```

Append at the bottom of `pyrrhon/tui/app.py` (module level):

```python
def run_tui(repo: str) -> None:
    """Entry point for the default (TUI) channel."""
    # Imported here, not at module top: repl.py is the single agent factory
    # and importing it lazily keeps tui importable without the REPL's deps.
    from pyrrhon.repl import build_agent

    repo_root = Path(repo).resolve()
    if not repo_root.is_dir():
        print(f"Not a directory: {repo_root}")
        raise SystemExit(1)
    try:
        agent = build_agent(repo_root)
    except MissingAPIKeyError as exc:
        print(exc)
        raise SystemExit(1)
    PyrrhonApp(repo_root=repo_root, agent=agent).run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tui_turns.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/tui/app.py tests/test_tui_turns.py
git commit -m "feat: agent turns in a Textual worker with slash dispatch and run_tui"
```

---

### Task 5: CLI `--text` flag, REPL joins the registry, docs

**Files:**
- Modify: `pyrrhon/cli.py`, `pyrrhon/repl.py`, `CLAUDE.md`
- Test: `tests/test_cli.py` (extend), `tests/test_init_and_repl.py` (extend)

**Interfaces:**
- Consumes: `run_tui` (Task 4); `dispatch`, `CommandContext` (Task 1); builtin commands (Task 2); everything `repl.py` already consumed in M0.
- Produces:
  - `pyrrhon.cli.main(argv: list[str] | None = None) -> None` — new `--text` flag: `--text` runs the M0 rich REPL, default launches `PyrrhonApp` via `run_tui`
  - `repl.ConsoleUI`: duck-typed `ui` for the text channel — `notify(text: str)` prints via the console; `last_citation: Citation | None` updated on every `Citation` event so `/code` works from the REPL too
  - `repl.run_repl(repo: str) -> None` — slash commands now go through `dispatch()` (the inline `/init` branch is deleted); `/quit` and `/exit` stay channel-level
  - `repl._turn(agent, history, user, console, ui) -> None` — same rendering as M0 plus citation tracking on `ui`
  - `repl.build_agent` is untouched — it remains the single agent factory

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_default_launches_tui(monkeypatch):
    launched: list[str] = []
    monkeypatch.setattr("pyrrhon.tui.app.run_tui", lambda repo: launched.append(repo))
    main(["some/repo"])
    assert launched == ["some/repo"]


def test_text_flag_launches_repl(monkeypatch):
    launched: list[str] = []
    monkeypatch.setattr("pyrrhon.repl.run_repl", lambda repo: launched.append(repo))
    main(["--text", "some/repo"])
    assert launched == ["some/repo"]
```

Append to `tests/test_init_and_repl.py`:

```python
async def test_console_ui_tracks_citations_for_code_command():
    import io

    from rich.console import Console

    from pyrrhon.core.events import Citation
    from pyrrhon.repl import ConsoleUI, _turn

    console = Console(file=io.StringIO())
    ui = ConsoleUI(console)
    fake = FakeLLM([LLMReply(text="greet lives at utils/helpers.py:1.")])
    agent = build_agent(FIXTURE, llm=fake)

    await _turn(agent, [], "where is greet?", console, ui)
    assert ui.last_citation == Citation(file="utils/helpers.py", line=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_init_and_repl.py -v`
Expected: FAIL — `test_text_flag_launches_repl` dies with argparse `SystemExit: 2` (`unrecognized arguments: --text`), `test_default_launches_tui` gets `SystemExit: 1` (the real `run_repl` runs and rejects the fake path), and the ConsoleUI test with `ImportError: cannot import name 'ConsoleUI' from 'pyrrhon.repl'`

- [ ] **Step 3: Write the implementation**

`pyrrhon/cli.py` — replace the whole file with:

```python
"""Command-line entry point: `pyrrhon [repo-path] [--text]`."""

from __future__ import annotations

import argparse

from pyrrhon import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pyrrhon",
        description="Talk to a codebase like a senior engineer is sitting next to you.",
    )
    parser.add_argument("repo", nargs="?", default=".", help="Path to the repo to discuss")
    parser.add_argument(
        "--text",
        action="store_true",
        help="Use the plain-text REPL instead of the TUI",
    )
    parser.add_argument("--version", action="version", version=f"pyrrhon {__version__}")
    args = parser.parse_args(argv)

    # Channels imported lazily so `--version` works without touching them.
    if args.text:
        from pyrrhon.repl import run_repl

        run_repl(args.repo)
    else:
        from pyrrhon.tui.app import run_tui

        run_tui(args.repo)
```

`pyrrhon/repl.py` — three scoped edits (leave `build_agent` exactly as M0/M1 left it):

1. Add to the import block (and delete the now-unused `from pyrrhon.commands.init_cmd import init_pyrrhon_dir`):

```python
from pyrrhon.commands import builtin  # noqa: F401 — registers /init, /model, /code
from pyrrhon.commands.registry import CommandContext, dispatch
```

2. Add the `ConsoleUI` adapter directly above `run_repl`:

```python
class ConsoleUI:
    """Duck-typed `ui` for CommandContext in the text channel."""

    def __init__(self, console: Console):
        self._console = console
        self.last_citation: Citation | None = None

    def notify(self, text: str) -> None:
        self._console.print(text)
```

3. Replace the `run_repl` and `_turn` functions with:

```python
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

    ui = ConsoleUI(console)
    ctx = CommandContext(repo_root=repo_root, agent=agent, ui=ui)
    console.print(
        f"[bold]Pyrrhon[/bold] — discussing [cyan]{repo_root.name}[/cyan]. "
        "Commands: /help, /quit"
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
        response = dispatch(user, ctx)
        if response is not None:
            console.print(response)
            continue
        asyncio.run(_turn(agent, history, user, console, ui))


async def _turn(agent: Agent, history: list[dict], user: str, console: Console, ui: ConsoleUI) -> None:
    async for event in agent.run_turn(history, user):
        if isinstance(event, ToolCallStarted):
            console.print(f"[dim]→ {event.name}({event.args})[/dim]")
        elif isinstance(event, SpeechChunk):
            console.print(Markdown(event.text))
        elif isinstance(event, Citation):
            ui.last_citation = event  # /code opens the most recent citation
            console.print(f"[green]📍 {event.file}:{event.line}[/green]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_init_and_repl.py -v`
Expected: all pass (4 in test_cli, 3 in test_init_and_repl)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (M0 + M1 suites intact, plus the 22 tests M2 added)

- [ ] **Step 6: Manual smoke test (needs GROQ_API_KEY set)**

Run: `uv run pyrrhon .` — confirm the TUI opens with transcript, code viewer, status bar (`mode: understand · fast: ...`), and focused input. Ask "what is this project?": tool calls appear dim, the answer renders as markdown, a 📍 chip appears and the code viewer jumps to the cited line with it highlighted. Then `/help` lists the commands, `/code` opens the citation in VS Code, `/model fast groq/llama-3.3-70b-versatile` updates the status bar, and `uv run pyrrhon . --text` still gives the M0 REPL where `/help` and `/init` work. If you have no key handy, confirm both channels exit with the `Set GROQ_API_KEY...` message instead.

- [ ] **Step 7: Record the new reality in CLAUDE.md**

In `CLAUDE.md`, update the commands section (adjusting from whatever wording M0/M1 landed) so the run/test bullets and current-state note read:

```markdown
- Run the app: `uv run pyrrhon [repo-path]` — launches the Textual TUI;
  add `--text` for the plain-text REPL (needs `GROQ_API_KEY` set, or
  configure another provider in `.pyrrhon.toml`)
- Run tests: `uv run pytest` (single test: `uv run pytest path::test_name`)

There is no lint config yet. Current state: M2 (Textual TUI + slash-command
registry) — see `docs/superpowers/plans/2026-07-03-pyrrhon-m2-textual-tui.md`.
```

- [ ] **Step 8: Commit**

```bash
git add pyrrhon/cli.py pyrrhon/repl.py tests/test_cli.py tests/test_init_and_repl.py CLAUDE.md
git commit -m "feat: TUI is the default channel, --text keeps the REPL, both share the command registry"
```

---

## Definition of Done (M2)

- `uv run pytest` fully green (registry unit tests need no UI; TUI covered by pilot tests with FakeLLM-backed agents).
- `uv run pyrrhon <some-repo>` opens the Textual app: questions stream into the transcript (dim tool lines, markdown answers, green 📍 citation chips), and every citation jumps the syntax-highlighted code viewer to the cited line.
- The UI never blocks during a turn — the agent runs in a Textual worker, and the input is re-enabled and re-focused the moment the turn ends.
- `/help`, `/init`, `/model <fast|deep> <provider>/<model>`, and `/code` work identically in the TUI and in `--text` mode, because both channels call the same `dispatch()`; unknown `/xyz` answers "Unknown command — try /help" instead of hitting the LLM.
- `/code` opens the most recent citation in VS Code via `code --goto <path>:<line>`; every failure mode (no citation yet, no `code` CLI, launch error) is an `ERROR:` string, never a crash.
- `uv run pyrrhon <repo> --text` is byte-for-byte the M0 REPL experience plus registry-backed slash commands; `repl.build_agent` remains the single agent factory.
- `core/` still has no channel imports (verify: `grep -rn "pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/` returns nothing).
