"""API-key storage: ~/.pyrrhon/credentials.toml, owner-only, env-always-wins.

Keys live here and ONLY here — never in config.toml (which users share and
commit) and never in logs. The file is chmod 0600 (best-effort on Windows,
where the user profile directory is already per-user). Environment variables
always take precedence: load_credentials uses os.environ.setdefault, so an
exported key beats a stored one.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path


def credentials_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".pyrrhon" / "credentials.toml"


def read_credentials(home: Path | None = None) -> dict[str, str]:
    path = credentials_path(home)
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {k: v for k, v in data.get("keys", {}).items() if isinstance(v, str)}


def save_credentials(updates: dict[str, str], home: Path | None = None) -> Path:
    path = credentials_path(home)
    merged = {**read_credentials(home), **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    # json.dumps produces a valid TOML basic string (quotes + escapes handled).
    lines = ["# Pyrrhon API keys — managed by `pyrrhon --setup`. Env vars win.", "[keys]"]
    lines += [f"{name} = {json.dumps(value)}" for name, value in sorted(merged.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows: chmod is limited; the profile dir is already per-user
    return path


def load_credentials(home: Path | None = None) -> dict[str, str]:
    stored = read_credentials(home)
    for name, value in stored.items():
        os.environ.setdefault(name, value)
    return stored
