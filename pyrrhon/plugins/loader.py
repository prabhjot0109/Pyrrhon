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

import importlib.util
import logging
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from pyrrhon.config.trust import read_trust_file
from pyrrhon.config.settings import (
    BUILTIN_PROVIDERS,
    MCPServerConfig,
    ProviderConfig,
    Settings,
)
from pyrrhon.core.tools.base import Tool

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


def read_trusted(repo_root: Path) -> set[str]:
    """Plugin names the user has trusted for this repo (<repo>/.pyrrhon/trusted).

    Delegates to the M11 parser rather than reading every non-empty line: the
    same file now also holds `kind:key=digest` grant lines for repo config and
    soul markdown, and treating one of those as a plugin name would be a quiet
    lie in whichever direction the caller happens to compare.
    """
    return set(read_trust_file(repo_root).plugins)


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


def merge_plugin_settings(settings: Settings, plugins: list[LoadedPlugin]) -> Settings:
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
            try:
                mcp_servers[name] = MCPServerConfig.model_validate(cfg)
            except ValidationError as exc:
                log.warning(
                    "plugin %s: invalid mcp server %r (%s) — ignored",
                    plugin.manifest.name, name, exc,
                )
    return settings.model_copy(
        update={"providers": providers, "mcp_servers": mcp_servers}
    )
