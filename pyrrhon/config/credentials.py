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
import re
import tomllib
from pathlib import Path

# A bare TOML key, which is also exactly what a POSIX environment variable name
# may be. Anything else produces a line `read_credentials` cannot parse, and a
# single one of those makes the WHOLE store raise on the next read — silently
# losing every key already in it.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    """Merge `updates` into the store, writing owner-only from the first byte.

    os.open with 0o600 rather than write_text-then-chmod: the old order left a
    window where a freshly created key file carried the process umask.

    Names are validated before anything is touched, so a rejected write leaves
    the existing store intact rather than half-rewritten.
    """
    for name in updates:
        if not _ENV_NAME_RE.match(name):
            raise ValueError(
                f"'{name}' is not a valid environment variable name; "
                "nothing was written."
            )
    path = credentials_path(home)
    merged = {**read_credentials(home), **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    # json.dumps produces a valid TOML basic string (quotes + escapes handled).
    lines = ["# Pyrrhon API keys — managed by `pyrrhon --setup`. Env vars win.", "[keys]"]
    lines += [f"{name} = {json.dumps(value)}" for name, value in sorted(merged.items())]
    body = "\n".join(lines) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
    try:
        path.chmod(0o600)  # tighten an already-existing file O_CREAT left alone
    except OSError:
        pass  # Windows: chmod is limited; the profile dir is already per-user
    return path


def load_credentials(home: Path | None = None) -> dict[str, str]:
    stored = read_credentials(home)
    for name, value in stored.items():
        os.environ.setdefault(name, value)
    return stored
