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
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

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
