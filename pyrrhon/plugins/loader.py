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
