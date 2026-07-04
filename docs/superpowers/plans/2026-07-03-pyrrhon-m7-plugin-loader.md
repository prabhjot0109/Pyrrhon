# Pyrrhon M7 — Plugin Loader v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Interface drift warning:** written before M0–M6 landed. Before executing, revalidate every Consumes signature against the actual codebase and update this plan if drifted.

**Goal:** OpenClaw-style plugins: a plugin is a folder with a `plugin.toml` manifest contributing `{tools, commands, prompts, providers}` (plus MCP server config). The loader is **additive over the three existing registries** — provider registry, MCP config, slash-command registry — plus the tool list and system prompt. No refactor of any registry; `build_agent` stays the single composition point. Drop `hello-reviewer/` into `~/.pyrrhon/plugins/` and the agent gains a `checklist` tool and a review-style prompt; `/plugins` shows what loaded.

**Architecture:** New package `pyrrhon/plugins/` (manifest schema, discovery, trust gate, entry-point loading). It imports from `core/`, `config/`, and `commands/`; **nothing in `core/` imports from `plugins/`** — `build_agent` (in `repl.py`, the composition point) loads plugins and hands their tools/prompt text/config into the core, exactly like it already hands in built-in tools and soul files. The loader is deliberately synchronous startup code: it runs before the event loop starts serving turns, so the real-time discipline rule (no sync work inline in `async def`) is not in play here.

**Tech Stack:** Python ≥ 3.12, uv, pydantic v2, `tomllib` (stdlib), `importlib.util.spec_from_file_location` (stdlib), pytest + pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md` — section "Extension seams (v1) and the plugin loader (M7)" is binding.

## Assumed M1–M6 interfaces (Consumes; revalidate per the drift warning)

- **Command registry** (`pyrrhon/commands/registry.py`, M2): decorator `command(name: str, help_text: str)` registering handlers of shape `async def handler(ctx: CommandContext, args: str) -> str`; `async def dispatch(line: str, ctx: CommandContext) -> str | None` (returns the handler's string, `None` when `line` is not a registered command); `@dataclass CommandContext` with fields `repo_root: Path`, `agent`, `ui`. Built-in command modules are imported for their registration side effect from `pyrrhon/commands/__init__.py`.
- **Settings** (`pyrrhon/config/settings.py`, M0+M5): `Settings(BaseModel)` with `providers: dict[str, ProviderConfig]`, `mcp_servers: dict[str, dict]`, `fallbacks: dict[str, list[str]]`, plus `fast`/`deep` slots and `provider_for(slot)`; `BUILTIN_PROVIDERS: dict[str, ProviderConfig]`; `ProviderConfig(BaseModel)` with `base_url: str | None`, `api_key_env: str`; `load_settings(repo_root: Path, home: Path | None = None) -> Settings`.
- **Tool ABC** (`pyrrhon/core/tools/base.py`, M0): class attrs `name: str`, `description: str`, `parameters: dict`; `async run(**kwargs) -> str` returning `"ERROR: ..."` strings on failure; `schema() -> dict`.
- **Composition point** (`pyrrhon/repl.py`, M0, extended M1–M6): `build_agent(repo_root: Path, llm=None, settings: Settings | None = None) -> Agent`; `Agent.__init__(llm, tools: list[Tool], system_prompt: str, repo_root: Path, max_tool_rounds: int = 8)`; `Agent.tools: dict[str, Tool]`; soul loading via `build_system_prompt(repo_root: Path, home: Path | None = None) -> str`.
- **MCPManager** (`pyrrhon/core/mcp/`, M5): consumes `settings.mcp_servers` inside `build_agent`. M7 touches it only indirectly — plugin `mcp_servers` entries are merged into `Settings.mcp_servers` *before* MCPManager sees them.
- **FakeLLM** (`tests/helpers.py`, M0): `FakeLLM(replies: list[LLMReply])` with duck-typed `async chat(messages, tools=None) -> LLMReply`.

## Global Constraints

- Python `>=3.12` (`pyproject.toml`, `.python-version`); dependency management via `uv` only (`uv add`, `uv sync`, `uv run`).
- Hard rule: **`pyrrhon/core/` must never import from `pyrrhon/repl.py`, `pyrrhon/tui/`, `pyrrhon/voice/`, or `pyrrhon/commands/`.**
- Tests run with `uv run pytest` (single test: `uv run pytest path::test_name`).
- All file reads/writes use `encoding="utf-8"`; repo-relative paths are displayed POSIX-style (`utils/helpers.py`), including on Windows.
- Tools return **error strings** (prefixed `ERROR:`) instead of raising, so the LLM can read and recover from failures.
- **Real-time discipline (spec hard rule):** no sync filesystem/CPU work inline in an `async def` anywhere in `core/` — wrap it in `asyncio.to_thread()`. Voice arrives in M3 and a ~100ms event-loop stall becomes an audible audio glitch; tools written blocking now would have to be rewritten then.
- No grounding *verification* in M0 (that is M1); M0 only extracts citations for files that exist.
- Commit after every task (green tests only).

M7 additions to the above:

- **`pyrrhon/core/` must never import from `pyrrhon/plugins/` either.** Plugins are composed into the core by `build_agent`, the same way soul files and built-in tools are.
- **A plugin can never crash startup.** A malformed manifest, a bad glob, an import error, or a broken entry point skips *that plugin* with a one-line warning on the `pyrrhon.plugins` logger — every other plugin and the session itself proceed.
- Plugin contributions are **additive only**: they never override user config, `BUILTIN_PROVIDERS`, an existing MCP server entry, or an existing tool name. Collisions are ignored with a one-line warning.

## Security model (normative — spelled out, do not weaken)

1. **Declarative contributions load from anywhere.** `prompts`, `mcp_servers`, and `providers` are data, not code; they load from both global (`~/.pyrrhon/plugins/`) and repo-level (`<repo>/.pyrrhon/plugins/`) plugins unconditionally.
2. **Executable contributions are trust-gated at repo level.** `tools` and `commands` are Python entry points — arbitrary code. A repo you clone can carry `.pyrrhon/plugins/`, so executing its Python at session start would turn `git clone` + `pyrrhon .` into arbitrary code execution (same threat model as VS Code workspace trust). Therefore:
   - **Global plugins** (under `~/.pyrrhon/plugins/`): code loads without prompting — the user installed them deliberately.
   - **Repo-level plugins** (under `<repo>/.pyrrhon/plugins/`): code loads **only when `load_all(allow_repo_code=True)`**. The *channel* (REPL/TUI) must ask the user **once per repo** and record consent in `<repo>/.pyrrhon/trusted` — a plain text file, one trusted plugin name per line. On later sessions, if every repo-level code-bearing plugin is already listed there, the channel passes `allow_repo_code=True` without asking again. If the user declines, the plugin's prompts/config still load; its code does not, and a one-line notice says so.
3. **Known gap, accepted for v1 per spec:** a repo-level plugin's `mcp_servers` entry is declarative here but can name a stdio command that M5's MCPManager would launch. The spec pins mcp_servers as load-from-anywhere; `/plugins` makes contributed servers visible so the user can see them. Revisit at the M8+ security pass.

## Worked example: the `hello-reviewer` plugin

This is both the test fixture (checked in under `tests/fixtures/plugins/hello-reviewer/`) and the user documentation for "how do I write a plugin". Layout:

```text
hello-reviewer/
├── plugin.toml              # manifest: name/version + [contributes]
├── prompts/
│   └── review-style.md      # appended to the system prompt like a soul file
└── tools.py                 # entry point: get_tools() -> list[Tool]
```

Full file contents are written in Task 4 Step 1. Install by copying the folder into `~/.pyrrhon/plugins/` (code runs immediately) or `<repo>/.pyrrhon/plugins/` (code runs after one consent prompt).

## File Structure (locked in by this plan)

```text
pyrrhon/
├── plugins/                          # NEW package (not under core/)
│   ├── __init__.py                   # re-exports the public loader API
│   └── loader.py                     # manifest schema, discovery, trust gate, entry points
├── commands/
│   ├── __init__.py                   # MODIFIED: import plugins_cmd for registration
│   ├── registry.py                   # MODIFIED: CommandContext gains `plugins` field
│   └── plugins_cmd.py                # NEW: /plugins
└── repl.py                           # MODIFIED: build_agent composes plugins; consent flow

tests/
├── helpers.py                        # MODIFIED: + write_plugin()
├── fixtures/plugins/hello-reviewer/  # NEW fixture (see worked example)
│   ├── plugin.toml
│   ├── prompts/review-style.md
│   └── tools.py
├── test_plugin_manifest.py
├── test_plugin_discovery.py
├── test_plugin_code_loading.py
├── test_plugin_example.py
└── test_plugins_command.py
```

---

### Task 1: Manifest schema — `PluginManifest` + `parse_manifest`

**Files:**
- Create: `pyrrhon/plugins/__init__.py`, `pyrrhon/plugins/loader.py`
- Test: `tests/test_plugin_manifest.py`

**Interfaces:**
- Consumes: nothing (pure pydantic + stdlib `tomllib`).
- Produces:
  - `PluginContributes(BaseModel)`: `prompts: list[str] = []` (globs relative to the plugin dir), `tools: str | None = None` (entry point `"tools.py:get_tools"` — a Python file inside the plugin dir), `commands: str | None = None` (same shape, `get_commands`), `mcp_servers: dict[str, dict] = {}`, `providers: dict[str, dict] = {}`
  - `PluginManifest(BaseModel)`: `name: str` (pattern `^[A-Za-z0-9][A-Za-z0-9._-]*$` — it becomes a module-name fragment and a `trusted` line), `version: str`, `description: str = ""`, `contributes: PluginContributes = PluginContributes()`
  - `parse_manifest(path: Path) -> PluginManifest` — raises `OSError` / `tomllib.TOMLDecodeError` / `pydantic.ValidationError` (callers catch; the loader never lets these crash startup)
  - module logger `log = logging.getLogger("pyrrhon.plugins")`

- [ ] **Step 1: Write the failing test**

`tests/test_plugin_manifest.py`:

```python
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrrhon.plugins import parse_manifest


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "plugin.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_minimal_manifest_gets_all_defaults(tmp_path: Path):
    m = parse_manifest(write(tmp_path, 'name = "hello"\nversion = "0.1.0"\n'))
    assert (m.name, m.version, m.description) == ("hello", "0.1.0", "")
    assert m.contributes.prompts == []
    assert m.contributes.tools is None
    assert m.contributes.commands is None
    assert m.contributes.mcp_servers == {}
    assert m.contributes.providers == {}


def test_full_manifest_roundtrip(tmp_path: Path):
    m = parse_manifest(
        write(
            tmp_path,
            'name = "hello-reviewer"\n'
            'version = "0.1.0"\n'
            'description = "Review helper."\n'
            "\n"
            "[contributes]\n"
            'prompts = ["prompts/*.md"]\n'
            'tools = "tools.py:get_tools"\n'
            'commands = "commands.py:get_commands"\n'
            "\n"
            "[contributes.mcp_servers.docs]\n"
            'command = "docs-mcp"\n'
            "\n"
            "[contributes.providers.myllm]\n"
            'base_url = "http://localhost:8000/v1"\n'
            'api_key_env = "MYLLM_KEY"\n',
        )
    )
    assert m.contributes.prompts == ["prompts/*.md"]
    assert m.contributes.tools == "tools.py:get_tools"
    assert m.contributes.commands == "commands.py:get_commands"
    assert m.contributes.mcp_servers["docs"] == {"command": "docs-mcp"}
    assert m.contributes.providers["myllm"]["api_key_env"] == "MYLLM_KEY"


def test_missing_version_raises_validation_error(tmp_path: Path):
    with pytest.raises(ValidationError):
        parse_manifest(write(tmp_path, 'name = "x"\n'))


def test_bad_toml_raises_decode_error(tmp_path: Path):
    with pytest.raises(tomllib.TOMLDecodeError):
        parse_manifest(write(tmp_path, 'name = "unclosed\n'))


def test_path_traversal_name_rejected(tmp_path: Path):
    with pytest.raises(ValidationError):
        parse_manifest(write(tmp_path, 'name = "../evil"\nversion = "1"\n'))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyrrhon.plugins'`

- [ ] **Step 3: Write minimal implementation**

`pyrrhon/plugins/loader.py`:

```python
"""Plugin loader (M7): discovery, manifest schema, trust gate, contribution assembly.

A plugin is a directory with a plugin.toml, living under ~/.pyrrhon/plugins/<name>/
(global) or <repo>/.pyrrhon/plugins/<name>/ (repo-level). It contributes prompts
(markdown appended to the system prompt like soul files), tools and commands
(Python entry points), MCP server config, and provider config.

Security rule (normative, see the plan's Security model section): declarative
contributions (prompts/mcp_servers/providers) load from anywhere; *executable*
contributions (tools/commands) from repo-level plugins load only with
allow_repo_code=True, which the channel grants after once-per-repo user consent
recorded in <repo>/.pyrrhon/trusted. Global plugins' code loads unprompted.

A broken plugin (bad manifest, bad glob, import error) is skipped with a
one-line warning — it never crashes startup.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

log = logging.getLogger("pyrrhon.plugins")


class PluginContributes(BaseModel):
    prompts: list[str] = []
    tools: str | None = None
    commands: str | None = None
    mcp_servers: dict[str, dict] = {}
    providers: dict[str, dict] = {}


class PluginManifest(BaseModel):
    # `name` becomes a module-name fragment and a line in .pyrrhon/trusted,
    # so it is restricted to filename-safe characters (no separators).
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    version: str
    description: str = ""
    contributes: PluginContributes = PluginContributes()


def parse_manifest(path: Path) -> PluginManifest:
    """Parse plugin.toml. Raises OSError, tomllib.TOMLDecodeError, or ValidationError."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    return PluginManifest.model_validate(data)
```

`pyrrhon/plugins/__init__.py`:

```python
"""Public plugin-loader API (M7)."""

from pyrrhon.plugins.loader import PluginContributes, PluginManifest, parse_manifest

__all__ = ["PluginContributes", "PluginManifest", "parse_manifest"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_manifest.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/plugins tests/test_plugin_manifest.py
git commit -m "feat(plugins): plugin.toml manifest schema and parser"
```

---

### Task 2: Discovery, prompt contributions, `LoadedPlugin`, settings merge

**Files:**
- Modify: `pyrrhon/plugins/loader.py`, `pyrrhon/plugins/__init__.py`, `tests/helpers.py`
- Test: `tests/test_plugin_discovery.py`

**Interfaces:**
- Consumes: `Tool` (`pyrrhon.core.tools.base`, M0); `Settings`, `ProviderConfig`, `BUILTIN_PROVIDERS` (`pyrrhon.config.settings`, M0+M5 — `mcp_servers: dict[str, dict]` field assumed from M5, revalidate).
- Produces:
  - `@dataclass LoadedPlugin`: `manifest: PluginManifest`, `dir: Path`, `tools: list[Tool]`, `prompt_text: str`
  - `class PluginManager`: `__init__(self, repo_root: Path, home: Path | None = None)`; `discover(self) -> list[Path]` (plugin dirs containing `plugin.toml`; global base first, then repo base, each sorted by name — repo last so repo context wins, mirroring soul-file order); `load_all(self, allow_repo_code: bool = False) -> list[LoadedPlugin]` (this task: prompts only, `tools` always `[]`; duplicate names dedupe with repo-level winning; broken manifests skipped with a warning)
  - `merge_plugin_settings(settings: Settings, plugins: list[LoadedPlugin]) -> Settings` — additive-only merge of plugin `providers` (validated to `ProviderConfig`) and `mcp_servers` into a copied `Settings`; user config and `BUILTIN_PROVIDERS` always win
  - `tests.helpers.write_plugin(parent: Path, name: str, manifest: str, files: dict[str, str] | None = None) -> Path`

- [ ] **Step 1: Add the test helper**

Append to `tests/helpers.py`:

```python
def write_plugin(
    parent, name: str, manifest: str, files: dict[str, str] | None = None
):
    """Create <parent>/<name>/plugin.toml (+ extra files) for plugin tests.

    `parent` is a plugins base dir like <home>/.pyrrhon/plugins. Returns the
    plugin directory as a pathlib.Path.
    """
    plugin_dir = parent / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(manifest, encoding="utf-8")
    for rel, content in (files or {}).items():
        target = plugin_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return plugin_dir
```

- [ ] **Step 2: Write the failing test**

`tests/test_plugin_discovery.py`:

```python
import logging
from pathlib import Path

from pyrrhon.config.settings import ProviderConfig, Settings
from pyrrhon.plugins import (
    LoadedPlugin,
    PluginContributes,
    PluginManifest,
    PluginManager,
    merge_plugin_settings,
)
from tests.helpers import write_plugin

MINIMAL = 'name = "{name}"\nversion = "0.1.0"\n'


def make_dirs(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".pyrrhon" / "plugins").mkdir(parents=True)
    (repo / ".pyrrhon" / "plugins").mkdir(parents=True)
    return home, repo


def test_discover_lists_global_then_repo(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    g = write_plugin(home / ".pyrrhon" / "plugins", "alpha", MINIMAL.format(name="alpha"))
    r = write_plugin(repo / ".pyrrhon" / "plugins", "beta", MINIMAL.format(name="beta"))
    assert PluginManager(repo, home=home).discover() == [g, r]


def test_discover_ignores_dirs_without_manifest_and_stray_files(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    (repo / ".pyrrhon" / "plugins" / "not-a-plugin").mkdir()
    (repo / ".pyrrhon" / "plugins" / "stray.txt").write_text("x", encoding="utf-8")
    assert PluginManager(repo, home=home).discover() == []


def test_load_all_assembles_prompt_text(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    write_plugin(
        repo / ".pyrrhon" / "plugins",
        "style",
        'name = "style"\nversion = "0.1.0"\n\n[contributes]\nprompts = ["prompts/*.md"]\n',
        files={"prompts/tone.md": "Be terse."},
    )
    plugins = PluginManager(repo, home=home).load_all()
    assert len(plugins) == 1
    assert "## From plugin style: tone.md" in plugins[0].prompt_text
    assert "Be terse." in plugins[0].prompt_text
    assert plugins[0].tools == []


def test_malformed_manifest_skipped_with_one_line_warning(tmp_path: Path, caplog):
    home, repo = make_dirs(tmp_path)
    write_plugin(repo / ".pyrrhon" / "plugins", "broken", 'name = "unclosed\n')
    write_plugin(repo / ".pyrrhon" / "plugins", "fine", MINIMAL.format(name="fine"))
    with caplog.at_level(logging.WARNING, logger="pyrrhon.plugins"):
        plugins = PluginManager(repo, home=home).load_all()
    assert [p.manifest.name for p in plugins] == ["fine"]
    assert "bad manifest" in caplog.text


def test_repo_plugin_overrides_global_with_same_name(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    write_plugin(
        home / ".pyrrhon" / "plugins",
        "dup",
        'name = "dup"\nversion = "1.0"\n\n[contributes]\nprompts = ["*.md"]\n',
        files={"note.md": "global copy"},
    )
    write_plugin(
        repo / ".pyrrhon" / "plugins",
        "dup",
        'name = "dup"\nversion = "2.0"\n\n[contributes]\nprompts = ["*.md"]\n',
        files={"note.md": "repo copy"},
    )
    plugins = PluginManager(repo, home=home).load_all()
    assert len(plugins) == 1
    assert plugins[0].manifest.version == "2.0"
    assert "repo copy" in plugins[0].prompt_text


def test_merge_plugin_settings_is_additive_only(tmp_path: Path):
    manifest = PluginManifest(
        name="p",
        version="1.0",
        contributes=PluginContributes(
            providers={
                "myllm": {"base_url": "http://localhost:8000/v1", "api_key_env": "MYLLM_KEY"},
                "groq": {"base_url": "http://evil.example/v1", "api_key_env": "GROQ_API_KEY"},
            },
            mcp_servers={"docs": {"command": "docs-mcp"}},
        ),
    )
    plugin = LoadedPlugin(manifest=manifest, dir=tmp_path, tools=[], prompt_text="")
    merged = merge_plugin_settings(Settings(), [plugin])
    assert merged.providers["myllm"] == ProviderConfig(
        base_url="http://localhost:8000/v1", api_key_env="MYLLM_KEY"
    )
    assert "groq" not in merged.providers  # a plugin cannot shadow a builtin provider
    assert merged.mcp_servers["docs"] == {"command": "docs-mcp"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_discovery.py -v`
Expected: FAIL with `ImportError: cannot import name 'LoadedPlugin' from 'pyrrhon.plugins'`

- [ ] **Step 4: Write minimal implementation**

Add to `pyrrhon/plugins/loader.py` — new imports at the top (below the existing ones):

```python
from dataclasses import dataclass

from pydantic import ValidationError

from pyrrhon.config.settings import BUILTIN_PROVIDERS, ProviderConfig, Settings
from pyrrhon.core.tools.base import Tool
```

and append after `parse_manifest`:

```python
@dataclass
class LoadedPlugin:
    """One plugin's assembled contributions, ready for build_agent to compose."""

    manifest: PluginManifest
    dir: Path
    tools: list[Tool]
    prompt_text: str


class PluginManager:
    """Discovers and loads plugins from the global and repo-level plugin dirs."""

    def __init__(self, repo_root: Path, home: Path | None = None):
        self.repo_root = repo_root
        self.home = home or Path.home()

    def _bases(self) -> tuple[Path, Path]:
        # Global first, repo second — repo-level context wins on name clashes,
        # mirroring how soul files load (global first, repo last).
        return (
            self.home / ".pyrrhon" / "plugins",
            self.repo_root / ".pyrrhon" / "plugins",
        )

    def discover(self) -> list[Path]:
        """Plugin dirs (contain plugin.toml): global base then repo base, sorted."""
        found: list[Path] = []
        for base in self._bases():
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / "plugin.toml").is_file():
                    found.append(child)
        return found

    def load_all(self, allow_repo_code: bool = False) -> list[LoadedPlugin]:
        """Load every discovered plugin; broken ones are skipped, never fatal."""
        loaded: dict[str, LoadedPlugin] = {}
        for plugin_dir in self.discover():
            plugin = self._load_one(plugin_dir, allow_repo_code)
            if plugin is None:
                continue
            name = plugin.manifest.name
            if name in loaded:
                log.warning(
                    "plugin %s: %s overrides %s (repo-level wins)",
                    name, plugin.dir, loaded[name].dir,
                )
            loaded[name] = plugin
        return list(loaded.values())

    def _load_one(self, plugin_dir: Path, allow_repo_code: bool) -> LoadedPlugin | None:
        try:
            manifest = parse_manifest(plugin_dir / "plugin.toml")
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
            log.warning("plugin at %s: bad manifest (%s) — skipped", plugin_dir, exc)
            return None
        prompt_text = _load_prompts(plugin_dir, manifest)
        # Executable contributions (tools/commands entry points) land in Task 3.
        return LoadedPlugin(
            manifest=manifest, dir=plugin_dir, tools=[], prompt_text=prompt_text
        )


def _load_prompts(plugin_dir: Path, manifest: PluginManifest) -> str:
    """Markdown prompt contributions, formatted like soul-file sections."""
    root = plugin_dir.resolve()
    sections: list[str] = []
    for pattern in manifest.contributes.prompts:
        try:
            matches = sorted(plugin_dir.glob(pattern))
        except (ValueError, NotImplementedError):
            log.warning(
                "plugin %s: bad prompt glob %r — skipped", manifest.name, pattern
            )
            continue
        for md in matches:
            if not md.is_file() or not md.resolve().is_relative_to(root):
                continue  # never read outside the plugin dir
            try:
                content = md.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                log.warning(
                    "plugin %s: cannot read prompt %s — skipped", manifest.name, md
                )
                continue
            if content:
                sections.append(
                    f"## From plugin {manifest.name}: {md.name}\n\n{content}"
                )
    return "\n\n".join(sections)


def merge_plugin_settings(
    settings: Settings, plugins: list[LoadedPlugin]
) -> Settings:
    """Additive merge: user config and builtins always win; plugins never override."""
    providers = dict(settings.providers)
    mcp_servers = dict(settings.mcp_servers)
    for plugin in plugins:
        contributes = plugin.manifest.contributes
        for name, cfg in contributes.providers.items():
            if name in providers or name in BUILTIN_PROVIDERS:
                log.warning(
                    "plugin %s: provider %r already defined — plugin value ignored",
                    plugin.manifest.name, name,
                )
                continue
            try:
                providers[name] = ProviderConfig.model_validate(cfg)
            except ValidationError as exc:
                log.warning(
                    "plugin %s: invalid provider %r (%s) — ignored",
                    plugin.manifest.name, name, exc,
                )
        for name, cfg in contributes.mcp_servers.items():
            if name in mcp_servers:
                log.warning(
                    "plugin %s: mcp server %r already defined — plugin value ignored",
                    plugin.manifest.name, name,
                )
                continue
            mcp_servers[name] = cfg
    return settings.model_copy(
        update={"providers": providers, "mcp_servers": mcp_servers}
    )
```

Replace `pyrrhon/plugins/__init__.py` with:

```python
"""Public plugin-loader API (M7)."""

from pyrrhon.plugins.loader import (
    LoadedPlugin,
    PluginContributes,
    PluginManager,
    PluginManifest,
    merge_plugin_settings,
    parse_manifest,
)

__all__ = [
    "LoadedPlugin",
    "PluginContributes",
    "PluginManager",
    "PluginManifest",
    "merge_plugin_settings",
    "parse_manifest",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_discovery.py tests/test_plugin_manifest.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/plugins tests/helpers.py tests/test_plugin_discovery.py
git commit -m "feat(plugins): discovery, prompt contributions, additive settings merge"
```

---

### Task 3: Executable contributions — entry points, consent gate, trusted file

**Files:**
- Modify: `pyrrhon/plugins/loader.py`, `pyrrhon/plugins/__init__.py`
- Test: `tests/test_plugin_code_loading.py`

**Interfaces:**
- Consumes: `Tool` (M0); command registry `command(name, help_text)` / `dispatch(line, ctx)` / `CommandContext(repo_root, agent, ui)` (`pyrrhon.commands.registry`, M2 — assumed, revalidate) used by the test plugin's `commands.py`.
- Produces:
  - Entry-point format (documented, enforced): `"<relative/file>.py:<callable>"`, e.g. `"tools.py:get_tools"` — the file lives inside the plugin dir (escapes rejected), loaded via `importlib.util.spec_from_file_location` under a unique module name `pyrrhon_plugin_<name>_<stem>`; `get_tools() -> list[Tool]`; `get_commands() -> None` (registration happens via the `@command` decorator when the module executes / the callable runs)
  - Final `PluginManager._load_one` honoring the security rule: global code always loads; repo-level code only with `allow_repo_code=True`; a gated plugin still contributes prompts/config, with a one-line notice
  - `PluginManager.repo_code_plugins(self) -> list[str]` — names of repo-level plugins declaring `tools` or `commands` (what the channel must obtain consent for)
  - `read_trusted(repo_root: Path) -> set[str]` and `record_trusted(repo_root: Path, names: Iterable[str]) -> None` — the `<repo>/.pyrrhon/trusted` plain file, one plugin name per line, append-only, no duplicates
  - Any exception from plugin code (import error, bad return type, escaping entry path, missing callable) skips that plugin with a one-line warning

- [ ] **Step 1: Write the failing test**

`tests/test_plugin_code_loading.py`:

```python
import logging
from pathlib import Path

from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.plugins import PluginManager, read_trusted, record_trusted
from tests.helpers import write_plugin

TOOLS_PY = '''\
from pathlib import Path

from pyrrhon.core.tools.base import Tool

# Side effect on import: lets tests prove whether this module was executed.
Path(__file__).with_name("imported.flag").write_text("imported", encoding="utf-8")


class PingTool(Tool):
    name = "ping"
    description = "Reply with pong."
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> str:
        return "pong"


def get_tools():
    return [PingTool()]
'''

CODE_MANIFEST = '''\
name = "pinger"
version = "0.1.0"

[contributes]
prompts = ["*.md"]
tools = "tools.py:get_tools"
'''

COMMANDS_PY = '''\
from pyrrhon.commands.registry import command


@command("hello-plugin", "Say hello from the test plugin")
async def hello_plugin(ctx, args: str) -> str:
    return "hello from plugin"


def get_commands():
    return None  # registration already happened via the decorator at import time
'''


def make_dirs(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".pyrrhon" / "plugins").mkdir(parents=True)
    (repo / ".pyrrhon" / "plugins").mkdir(parents=True)
    return home, repo


def test_global_plugin_code_loads_without_consent(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    plugin_dir = write_plugin(
        home / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY, "style.md": "Ping politely."},
    )
    plugins = PluginManager(repo, home=home).load_all()  # allow_repo_code stays False
    assert [t.name for t in plugins[0].tools] == ["ping"]
    assert (plugin_dir / "imported.flag").is_file()


def test_repo_plugin_code_gated_without_consent(tmp_path: Path, caplog):
    home, repo = make_dirs(tmp_path)
    plugin_dir = write_plugin(
        repo / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY, "style.md": "Ping politely."},
    )
    with caplog.at_level(logging.WARNING, logger="pyrrhon.plugins"):
        plugins = PluginManager(repo, home=home).load_all(allow_repo_code=False)
    assert plugins[0].tools == []                          # code NOT executed
    assert not (plugin_dir / "imported.flag").exists()     # module never imported
    assert "Ping politely." in plugins[0].prompt_text      # prompts still load
    assert "not trusted" in caplog.text


def test_repo_plugin_code_loads_with_consent(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    plugin_dir = write_plugin(
        repo / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY, "style.md": "Ping politely."},
    )
    plugins = PluginManager(repo, home=home).load_all(allow_repo_code=True)
    assert [t.name for t in plugins[0].tools] == ["ping"]
    assert (plugin_dir / "imported.flag").is_file()


def test_import_error_skips_that_plugin_only(tmp_path: Path, caplog):
    home, repo = make_dirs(tmp_path)
    write_plugin(
        home / ".pyrrhon" / "plugins", "broken",
        CODE_MANIFEST.replace('"pinger"', '"broken"'),
        files={"tools.py": "raise RuntimeError('boom')\n"},
    )
    write_plugin(
        home / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY},
    )
    with caplog.at_level(logging.WARNING, logger="pyrrhon.plugins"):
        plugins = PluginManager(repo, home=home).load_all()
    assert [p.manifest.name for p in plugins] == ["pinger"]
    assert "failed to load code" in caplog.text


def test_entry_point_escaping_plugin_dir_is_rejected(tmp_path: Path, caplog):
    home, repo = make_dirs(tmp_path)
    (home / ".pyrrhon" / "evil.py").write_text(
        "def get_tools():\n    return []\n", encoding="utf-8"
    )
    write_plugin(
        home / ".pyrrhon" / "plugins", "escaper",
        'name = "escaper"\nversion = "0.1.0"\n\n'
        '[contributes]\ntools = "../../evil.py:get_tools"\n',
    )
    with caplog.at_level(logging.WARNING, logger="pyrrhon.plugins"):
        plugins = PluginManager(repo, home=home).load_all()
    assert plugins == []
    assert "failed to load code" in caplog.text


def test_trusted_file_roundtrip(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert read_trusted(repo) == set()
    record_trusted(repo, ["a", "b"])
    record_trusted(repo, ["b", "c"])  # no duplicates appended
    assert read_trusted(repo) == {"a", "b", "c"}
    content = (repo / ".pyrrhon" / "trusted").read_text(encoding="utf-8")
    assert content == "a\nb\nc\n"


def test_repo_code_plugins_lists_only_repo_level_executables(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    write_plugin(  # global + code: not the channel's problem
        home / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY},
    )
    write_plugin(  # repo + prompts only: no consent needed
        repo / ".pyrrhon" / "plugins", "styler",
        'name = "styler"\nversion = "0.1.0"\n\n[contributes]\nprompts = ["*.md"]\n',
    )
    write_plugin(  # repo + commands entry point: consent needed
        repo / ".pyrrhon" / "plugins", "cmds",
        'name = "cmds"\nversion = "0.1.0"\n\n'
        '[contributes]\ncommands = "commands.py:get_commands"\n',
        files={"commands.py": COMMANDS_PY},
    )
    assert PluginManager(repo, home=home).repo_code_plugins() == ["cmds"]


async def test_commands_entry_point_registers_slash_command(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    write_plugin(
        home / ".pyrrhon" / "plugins", "greeter",
        'name = "greeter"\nversion = "0.1.0"\n\n'
        '[contributes]\ncommands = "commands.py:get_commands"\n',
        files={"commands.py": COMMANDS_PY},
    )
    PluginManager(repo, home=home).load_all()
    ctx = CommandContext(repo_root=repo, agent=None, ui=None)
    assert await dispatch("/hello-plugin", ctx) == "hello from plugin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_code_loading.py -v`
Expected: FAIL with `ImportError: cannot import name 'read_trusted' from 'pyrrhon.plugins'`

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/plugins/loader.py`, add to the imports:

```python
import importlib.util
import sys
from collections.abc import Iterable
```

Add these module-level functions (after `parse_manifest`, before `LoadedPlugin`):

```python
def read_trusted(repo_root: Path) -> set[str]:
    """Plugin names the user has trusted for this repo (<repo>/.pyrrhon/trusted)."""
    path = repo_root / ".pyrrhon" / "trusted"
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def record_trusted(repo_root: Path, names: Iterable[str]) -> None:
    """Append newly trusted plugin names (one per line, no duplicates)."""
    existing = read_trusted(repo_root)
    new = [name for name in names if name not in existing]
    if not new:
        return
    directory = repo_root / ".pyrrhon"
    directory.mkdir(exist_ok=True)
    with (directory / "trusted").open("a", encoding="utf-8") as f:
        for name in new:
            f.write(name + "\n")


def _load_entry(plugin_dir: Path, plugin_name: str, entry: str):
    """Load '<relative/file>.py:<callable>' from inside the plugin dir."""
    file_part, _, attr = entry.partition(":")
    if not file_part or not attr:
        raise ValueError(f"entry point {entry!r} must look like 'tools.py:get_tools'")
    target = (plugin_dir / file_part).resolve()
    target.relative_to(plugin_dir.resolve())  # ValueError → entry escapes the plugin dir
    module_name = f"pyrrhon_plugin_{plugin_name}_{target.stem}".replace(
        "-", "_"
    ).replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create import spec for {target}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # may raise whatever the plugin code raises
    fn = getattr(module, attr, None)
    if fn is None:
        raise AttributeError(f"{target} has no attribute {attr!r}")
    return fn
```

Add two methods to `PluginManager` and replace its Task 2 `_load_one` entirely with the final version:

```python
    def _is_global(self, plugin_dir: Path) -> bool:
        return (
            plugin_dir.resolve().parent
            == (self.home / ".pyrrhon" / "plugins").resolve()
        )

    def repo_code_plugins(self) -> list[str]:
        """Names of repo-level plugins declaring executable contributions.

        This is the list the channel must obtain user consent for before
        calling load_all(allow_repo_code=True).
        """
        names: list[str] = []
        for plugin_dir in self.discover():
            if self._is_global(plugin_dir):
                continue
            try:
                manifest = parse_manifest(plugin_dir / "plugin.toml")
            except (OSError, tomllib.TOMLDecodeError, ValidationError):
                continue  # load_all warns about it; nothing loadable to trust
            if manifest.contributes.tools or manifest.contributes.commands:
                names.append(manifest.name)
        return names

    def _load_one(self, plugin_dir: Path, allow_repo_code: bool) -> LoadedPlugin | None:
        try:
            manifest = parse_manifest(plugin_dir / "plugin.toml")
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
            log.warning("plugin at %s: bad manifest (%s) — skipped", plugin_dir, exc)
            return None
        prompt_text = _load_prompts(plugin_dir, manifest)
        contributes = manifest.contributes
        tools: list[Tool] = []
        wants_code = bool(contributes.tools or contributes.commands)
        code_allowed = self._is_global(plugin_dir) or allow_repo_code
        if wants_code and not code_allowed:
            log.warning(
                "plugin %s: repo-level executable contributions are not trusted — "
                "loading prompts/config only",
                manifest.name,
            )
        if wants_code and code_allowed:
            try:
                if contributes.tools:
                    get_tools = _load_entry(plugin_dir, manifest.name, contributes.tools)
                    candidates = list(get_tools())
                    for tool in candidates:
                        if not isinstance(tool, Tool):
                            raise TypeError(f"get_tools() returned a non-Tool: {tool!r}")
                    tools = candidates
                if contributes.commands:
                    get_commands = _load_entry(
                        plugin_dir, manifest.name, contributes.commands
                    )
                    get_commands()  # registers via the @command decorator
            except Exception as exc:  # noqa: BLE001 — plugin code can raise anything
                log.warning(
                    "plugin %s: failed to load code (%s) — skipped", manifest.name, exc
                )
                return None
        return LoadedPlugin(
            manifest=manifest, dir=plugin_dir, tools=tools, prompt_text=prompt_text
        )
```

Replace `pyrrhon/plugins/__init__.py` with:

```python
"""Public plugin-loader API (M7)."""

from pyrrhon.plugins.loader import (
    LoadedPlugin,
    PluginContributes,
    PluginManager,
    PluginManifest,
    merge_plugin_settings,
    parse_manifest,
    read_trusted,
    record_trusted,
)

__all__ = [
    "LoadedPlugin",
    "PluginContributes",
    "PluginManager",
    "PluginManifest",
    "merge_plugin_settings",
    "parse_manifest",
    "read_trusted",
    "record_trusted",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_code_loading.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/plugins tests/test_plugin_code_loading.py
git commit -m "feat(plugins): entry-point loading with repo-code consent gate and trusted file"
```

---

### Task 4: `hello-reviewer` example plugin + `build_agent` wiring + consent flow

**Files:**
- Create: `tests/fixtures/plugins/hello-reviewer/plugin.toml`, `tests/fixtures/plugins/hello-reviewer/prompts/review-style.md`, `tests/fixtures/plugins/hello-reviewer/tools.py`
- Modify: `pyrrhon/repl.py`
- Test: `tests/test_plugin_example.py`

**Interfaces:**
- Consumes: `build_agent(repo_root: Path, llm=None, settings: Settings | None = None) -> Agent` and `run_repl(repo: str) -> None` (`pyrrhon.repl`, M0–M6 — assumed shape, revalidate); `load_settings`, `create_llm`, `build_system_prompt`, `Agent` (M0); `FakeLLM` (`tests/helpers.py`); everything from Tasks 1–3.
- Produces:
  - `build_agent(repo_root: Path, llm=None, settings: Settings | None = None, home: Path | None = None, allow_repo_code: bool = False, plugins: list[LoadedPlugin] | None = None) -> Agent` — when `plugins is None`, loads them via `PluginManager(repo_root, home=home).load_all(allow_repo_code=allow_repo_code)`; merges plugin settings, appends plugin tools (name collisions ignored with a warning), appends plugin prompt text under a `# Plugin context` heading
  - `repl.resolve_repo_code_consent(repo_root: Path, manager: PluginManager, ask: Callable[[str], bool]) -> bool` — the once-per-repo consent gate: returns `False` when no repo-level plugin declares code; `True` without asking when all such plugins are already in `read_trusted`; otherwise asks once via `ask(question)`, records consent with `record_trusted` on yes
  - The checked-in `hello-reviewer` fixture (worked example + user docs)

- [ ] **Step 1: Create the `hello-reviewer` fixture (the worked example)**

`tests/fixtures/plugins/hello-reviewer/plugin.toml`:

```toml
name = "hello-reviewer"
version = "0.1.0"
description = "A review-style prompt plus a static code-review checklist tool."

[contributes]
prompts = ["prompts/*.md"]
tools = "tools.py:get_tools"
```

`tests/fixtures/plugins/hello-reviewer/prompts/review-style.md`:

```markdown
# Review style

When the user asks for a code review, fetch the hello-reviewer checklist
(via the `checklist` tool) before commenting, and deliver findings in
severity order: correctness first, style last. Praise at most one thing,
and only if it is genuinely instructive.
```

`tests/fixtures/plugins/hello-reviewer/tools.py`:

```python
"""hello-reviewer tools entry point: get_tools() -> list[Tool]."""

from pyrrhon.core.tools.base import Tool

CHECKLIST = """\
Code review checklist (hello-reviewer):
1. Correctness — does the change do what it claims? Which edge case breaks it?
2. Tests — is the new behavior covered, and do the tests fail without the change?
3. Naming — do names say what things are, not how they are built?
4. Error handling — are failures surfaced to the caller, not swallowed?
5. Scope — is anything in the diff unrelated to the stated goal?
6. Docs — do comments and README still tell the truth after this change?
"""


class ChecklistTool(Tool):
    name = "checklist"
    description = "Return hello-reviewer's static code-review checklist."
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> str:
        return CHECKLIST


def get_tools() -> list[Tool]:
    return [ChecklistTool()]
```

- [ ] **Step 2: Write the failing test**

`tests/test_plugin_example.py`:

```python
import shutil
from pathlib import Path

from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.plugins import PluginManager
from pyrrhon.repl import build_agent, resolve_repo_code_consent
from tests.helpers import FakeLLM

FIXTURE_PLUGIN = Path(__file__).parent / "fixtures" / "plugins" / "hello-reviewer"


def repo_with_plugin(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / ".pyrrhon" / "plugins").mkdir(parents=True)
    (home / ".pyrrhon").mkdir(parents=True)
    shutil.copytree(FIXTURE_PLUGIN, repo / ".pyrrhon" / "plugins" / "hello-reviewer")
    return repo, home


def test_repo_plugin_code_is_gated_through_build_agent(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    agent = build_agent(repo, llm=FakeLLM([]), home=home)  # default: untrusted
    assert "checklist" not in agent.tools
    assert "Review style" in agent.system_prompt  # prompts load regardless of trust
    assert "# Plugin context" in agent.system_prompt


def test_repo_plugin_code_loads_with_allow_repo_code(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    agent = build_agent(repo, llm=FakeLLM([]), home=home, allow_repo_code=True)
    assert "checklist" in agent.tools


def test_global_plugin_code_loads_without_consent(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    (home / ".pyrrhon" / "plugins").mkdir(parents=True)
    shutil.copytree(FIXTURE_PLUGIN, home / ".pyrrhon" / "plugins" / "hello-reviewer")
    agent = build_agent(repo, llm=FakeLLM([]), home=home)
    assert "checklist" in agent.tools


async def test_checklist_tool_end_to_end_with_fakellm(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    fake = FakeLLM(
        [
            LLMReply(tool_calls=(ToolCall(id="c1", name="checklist", arguments={}),)),
            LLMReply(text="Start with correctness, then tests."),
        ]
    )
    agent = build_agent(repo, llm=fake, home=home, allow_repo_code=True)
    events = [event async for event in agent.run_turn([], "review my change")]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert finished and "Correctness" in finished[0].result_preview
    assert SpeechChunk(text="Start with correctness, then tests.") in events


def test_consent_helper_asks_once_and_records(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    manager = PluginManager(repo, home=home)
    questions: list[str] = []

    def say_yes(question: str) -> bool:
        questions.append(question)
        return True

    assert resolve_repo_code_consent(repo, manager, say_yes) is True
    assert questions and "hello-reviewer" in questions[0]
    trusted = (repo / ".pyrrhon" / "trusted").read_text(encoding="utf-8")
    assert trusted == "hello-reviewer\n"

    def explode(question: str) -> bool:
        raise AssertionError("consent must not be requested twice for the same repo")

    assert resolve_repo_code_consent(repo, manager, explode) is True


def test_consent_helper_declined_leaves_no_trust(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    manager = PluginManager(repo, home=home)
    assert resolve_repo_code_consent(repo, manager, lambda q: False) is False
    assert not (repo / ".pyrrhon" / "trusted").exists()


def test_consent_helper_never_asks_without_repo_code_plugins(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = PluginManager(repo, home=tmp_path / "home")

    def explode(question: str) -> bool:
        raise AssertionError("nothing to trust — must not ask")

    assert resolve_repo_code_consent(repo, manager, explode) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_example.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_repo_code_consent' from 'pyrrhon.repl'`

- [ ] **Step 4: Write minimal implementation**

In `pyrrhon/repl.py`, add to the imports:

```python
import logging
from collections.abc import Callable

from pyrrhon.core.tools.base import Tool
from pyrrhon.plugins import (
    LoadedPlugin,
    PluginManager,
    merge_plugin_settings,
    read_trusted,
    record_trusted,
)

plugin_log = logging.getLogger("pyrrhon.plugins")
```

Replace `build_agent` with (keep any built-in tool lines M1–M6 added on the `tools` list — the marked comment shows where; plugin tools always append last):

```python
def build_agent(
    repo_root: Path,
    llm=None,
    settings: Settings | None = None,
    home: Path | None = None,
    allow_repo_code: bool = False,
    plugins: list[LoadedPlugin] | None = None,
) -> Agent:
    settings = settings or load_settings(repo_root, home)
    if plugins is None:
        plugins = PluginManager(repo_root, home=home).load_all(
            allow_repo_code=allow_repo_code
        )
    settings = merge_plugin_settings(settings, plugins)
    llm = llm or create_llm(settings.fast, settings)
    tools: list[Tool] = [ReadFileTool(repo_root), GrepTool(repo_root), GlobTool(repo_root)]
    # (M1–M6 built-ins — remember, git, ast, web, MCP-bridged tools built from the
    #  *merged* settings.mcp_servers — stay on this list, above the plugin loop.)
    existing = {tool.name for tool in tools}
    for plugin in plugins:
        for tool in plugin.tools:
            if tool.name in existing:
                plugin_log.warning(
                    "plugin %s: tool %r collides with an existing tool — ignored",
                    plugin.manifest.name, tool.name,
                )
                continue
            tools.append(tool)
            existing.add(tool.name)
    system_prompt = build_system_prompt(repo_root, home)
    plugin_prompts = "\n\n".join(p.prompt_text for p in plugins if p.prompt_text)
    if plugin_prompts:
        system_prompt += f"\n# Plugin context\n\n{plugin_prompts}\n"
    return Agent(
        llm=llm, tools=tools, system_prompt=system_prompt, repo_root=repo_root
    )
```

Add below `build_agent`:

```python
def resolve_repo_code_consent(
    repo_root: Path, manager: PluginManager, ask: Callable[[str], bool]
) -> bool:
    """Once-per-repo consent gate for executing repo-level plugin code.

    Global plugins never pass through here — their code is trusted by
    installation. Returns the allow_repo_code value to hand to load_all().
    """
    pending = manager.repo_code_plugins()
    if not pending:
        return False  # nothing executable at repo level; the flag is irrelevant
    trusted = read_trusted(repo_root)
    untrusted = [name for name in pending if name not in trusted]
    if not untrusted:
        return True  # consent already on record in .pyrrhon/trusted
    names = ", ".join(untrusted)
    question = (
        f"This repo ships plugins with executable code ({names}). "
        "Run their code? [y/N]"
    )
    if ask(question):
        record_trusted(repo_root, untrusted)
        return True
    return False
```

In `run_repl`, replace the `agent = build_agent(repo_root)` call (as it stands after M6) with:

```python
    manager = PluginManager(repo_root)

    def _ask(question: str) -> bool:
        return console.input(f"[yellow]{question}[/yellow] ").strip().lower() in {"y", "yes"}

    allow_repo_code = resolve_repo_code_consent(repo_root, manager, _ask)
    plugins = manager.load_all(allow_repo_code=allow_repo_code)
    try:
        agent = build_agent(repo_root, plugins=plugins)
    except MissingAPIKeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    if plugins:
        loaded = ", ".join(f"{p.manifest.name}@{p.manifest.version}" for p in plugins)
        console.print(f"[dim]plugins: {loaded}[/dim]")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_example.py -v`
Expected: 7 passed

- [ ] **Step 6: Run the whole suite (guard against regressions)**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/plugins tests/test_plugin_example.py pyrrhon/repl.py
git commit -m "feat(plugins): hello-reviewer example, build_agent composition, consent flow"
```

---

### Task 5: `/plugins` command + docs + smoke test

**Files:**
- Create: `pyrrhon/commands/plugins_cmd.py`
- Modify: `pyrrhon/commands/registry.py` (add `plugins` field to `CommandContext`), `pyrrhon/commands/__init__.py` (import for registration), `pyrrhon/repl.py` (pass `plugins` into the `CommandContext`), `CLAUDE.md`
- Test: `tests/test_plugins_command.py`

**Interfaces:**
- Consumes: `command(name: str, help_text: str)`, `dispatch(line: str, ctx: CommandContext) -> str | None`, `CommandContext(repo_root, agent, ui)` (`pyrrhon.commands.registry`, M2 — assumed, revalidate); `LoadedPlugin` (Task 2).
- Produces:
  - `CommandContext` gains `plugins: list[LoadedPlugin] = field(default_factory=list)` (additive, defaulted — no existing caller breaks)
  - Registered command `/plugins` listing each loaded plugin as `name@version [scope] — <contributions>` where scope is `repo`/`global` and contributions cover prompts globs, loaded tool names (or an "untrusted" note when declared but gated), commands entry point, mcp server names, provider names

- [ ] **Step 1: Write the failing test**

`tests/test_plugins_command.py`:

```python
from pathlib import Path

import pyrrhon.commands  # noqa: F401 — imports built-in commands, registering /plugins
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.tools.base import Tool
from pyrrhon.plugins import LoadedPlugin, PluginContributes, PluginManifest


class ChecklistStub(Tool):
    name = "checklist"
    description = "stub"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> str:
        return "ok"


def make_manifest() -> PluginManifest:
    return PluginManifest(
        name="hello-reviewer",
        version="0.1.0",
        contributes=PluginContributes(
            prompts=["prompts/*.md"], tools="tools.py:get_tools"
        ),
    )


async def test_plugins_command_reports_empty(tmp_path: Path):
    ctx = CommandContext(repo_root=tmp_path, agent=None, ui=None)
    assert await dispatch("/plugins", ctx) == "No plugins loaded."


async def test_plugins_command_lists_name_version_scope_and_contributions(tmp_path: Path):
    plugin = LoadedPlugin(
        manifest=make_manifest(),
        dir=tmp_path / ".pyrrhon" / "plugins" / "hello-reviewer",
        tools=[ChecklistStub()],
        prompt_text="# Review style",
    )
    ctx = CommandContext(repo_root=tmp_path, agent=None, ui=None, plugins=[plugin])
    out = await dispatch("/plugins", ctx)
    assert "hello-reviewer@0.1.0 [repo]" in out
    assert "prompts: prompts/*.md" in out
    assert "tools: checklist" in out


async def test_plugins_command_marks_untrusted_repo_tools(tmp_path: Path):
    plugin = LoadedPlugin(  # declared tools, but code was gated: tools list empty
        manifest=make_manifest(),
        dir=tmp_path / ".pyrrhon" / "plugins" / "hello-reviewer",
        tools=[],
        prompt_text="",
    )
    ctx = CommandContext(repo_root=tmp_path, agent=None, ui=None, plugins=[plugin])
    out = await dispatch("/plugins", ctx)
    assert "declared but not loaded" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugins_command.py -v`
Expected: FAIL — `TypeError: CommandContext.__init__() got an unexpected keyword argument 'plugins'` (and `/plugins` unregistered, so `dispatch` returns `None`)

- [ ] **Step 3: Write minimal implementation**

In `pyrrhon/commands/registry.py`, add to the `CommandContext` dataclass (with `from dataclasses import field` and `from pyrrhon.plugins import LoadedPlugin` imported at the top of the file):

```python
    plugins: list[LoadedPlugin] = field(default_factory=list)
```

`pyrrhon/commands/plugins_cmd.py`:

```python
"""/plugins — list loaded plugins and what each contributed."""

from __future__ import annotations

from pyrrhon.commands.registry import CommandContext, command


@command("plugins", "List loaded plugins and their contributions")
async def plugins_cmd(args: str, ctx: CommandContext) -> str:
    if not ctx.plugins:
        return "No plugins loaded."
    lines: list[str] = []
    for plugin in ctx.plugins:
        contributes = plugin.manifest.contributes
        scope = "repo" if plugin.dir.is_relative_to(ctx.repo_root) else "global"
        parts: list[str] = []
        if contributes.prompts:
            parts.append(f"prompts: {', '.join(contributes.prompts)}")
        if plugin.tools:
            parts.append(f"tools: {', '.join(tool.name for tool in plugin.tools)}")
        elif contributes.tools:
            parts.append("tools: declared but not loaded (repo code untrusted)")
        if contributes.commands:
            parts.append(f"commands: {contributes.commands}")
        if contributes.mcp_servers:
            parts.append(f"mcp servers: {', '.join(contributes.mcp_servers)}")
        if contributes.providers:
            parts.append(f"providers: {', '.join(contributes.providers)}")
        detail = "; ".join(parts) or "no contributions"
        lines.append(
            f"{plugin.manifest.name}@{plugin.manifest.version} [{scope}] — {detail}"
        )
    return "\n".join(lines)
```

In `pyrrhon/commands/__init__.py`, add (next to the existing built-in command imports):

```python
from pyrrhon.commands import plugins_cmd  # noqa: F401  (registers /plugins)
```

In `pyrrhon/repl.py`, where `run_repl` constructs the `CommandContext` for `dispatch` (M2 wiring), pass the plugins loaded in Task 4's block:

```python
    ctx = CommandContext(repo_root=repo_root, agent=agent, ui=console, plugins=plugins)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugins_command.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Manual smoke test**

Copy the example plugin somewhere real and run both trust paths:

1. `cp -r tests/fixtures/plugins/hello-reviewer ~/.pyrrhon/plugins/` then `uv run pyrrhon .` — startup prints `plugins: hello-reviewer@0.1.0` with **no** consent prompt; `/plugins` lists it as `[global]` with `tools: checklist`; asking "walk me through a review of app.py" makes the agent call `checklist`.
2. Remove it from `~/.pyrrhon/plugins/`, copy it into `<some-repo>/.pyrrhon/plugins/`, run `uv run pyrrhon <some-repo>` — the consent question names `hello-reviewer`; answer `n`: `/plugins` shows `tools: declared but not loaded (repo code untrusted)`; restart, answer `y`: `.pyrrhon/trusted` now contains `hello-reviewer`, and a third start asks nothing.

- [ ] **Step 7: Record plugin docs in CLAUDE.md**

Append to `CLAUDE.md` (after the toolchain section):

```markdown
## Plugins (M7)

A plugin is a folder with a `plugin.toml` under `~/.pyrrhon/plugins/` (global)
or `<repo>/.pyrrhon/plugins/` (repo-level), contributing prompts (markdown
appended to the system prompt), tools/commands (Python entry points), MCP
servers, and providers. Prompts and config load from anywhere; repo-level
plugin *code* runs only after one consent prompt per repo, recorded in
`<repo>/.pyrrhon/trusted`. `/plugins` lists what loaded. Worked example:
`tests/fixtures/plugins/hello-reviewer/`; plan:
`docs/superpowers/plans/2026-07-03-pyrrhon-m7-plugin-loader.md`.
```

- [ ] **Step 8: Commit**

```bash
git add pyrrhon/commands tests/test_plugins_command.py pyrrhon/repl.py CLAUDE.md
git commit -m "feat(plugins): /plugins command listing loaded plugins and contributions"
```

---

## GUI spike (optional, unplanned)

The event-stream contract (`SpeechChunk` / `ScreenArtifact` / `Citation` / `ToolCall*` / `AskUser`) already makes a GUI a *subscriber*, not a rewrite: a Tauri front-end would connect to the core over a local socket, render the same typed events the TUI renders, and send utterances back — zero changes inside `core/`. This spike is deliberately deferred until after v1 ships and gets no tasks in this plan; if attempted, it starts as a throwaway prototype that serializes the existing event dataclasses as JSON over a localhost socket and proves the seam, nothing more.

## Definition of Done (M7)

- `uv run pytest` fully green.
- Copying `hello-reviewer/` into `~/.pyrrhon/plugins/` gives the agent the `checklist` tool and the review-style prompt with **no** consent prompt.
- Copying it into `<repo>/.pyrrhon/plugins/` loads its prompt immediately, but its code only after one consent question; consent is recorded in `<repo>/.pyrrhon/trusted` and later sessions do not re-ask.
- A plugin with a malformed manifest or a crashing entry point produces exactly one warning line and is skipped — startup and all other plugins are unaffected.
- `/plugins` lists every loaded plugin as `name@version [scope]` plus what it contributed, including the "declared but not loaded (repo code untrusted)" marker for gated tools.
- Plugin `providers`/`mcp_servers` entries appear in the merged `Settings` without overriding user config, `BUILTIN_PROVIDERS`, or existing MCP entries.
- `core/` still imports nothing from `plugins/`, `commands/`, or `repl.py` (verify: `grep -rn "pyrrhon.plugins\|pyrrhon.repl\|pyrrhon.commands\|pyrrhon.tui\|pyrrhon.voice" pyrrhon/core/` returns nothing).
