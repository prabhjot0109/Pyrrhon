"""Content-bound consent records for anything a repo supplies.

M7 gated repo-level plugin *code* behind a once-per-repo prompt recorded in
<repo>/.pyrrhon/trusted. M11 extends the same file to everything else a cloned
repo can hand us that runs, redirects, or writes the prompt: MCP server
commands, provider base URLs, a Piper TTS URL, and soul markdown.

Grants are bound to a SHA-256 of the granted VALUE, not to its name. Name-only
trust would let a repo approve `mcp_servers.indexer = node ./build.js`, wait,
and then swap the command for something else under the same already-trusted
name — which is the entire attack, one commit later.

The file keeps its old grammar readable: a line with no ':' is a legacy plugin
name and still means what it always meant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


def digest_value(value: object) -> str:
    """SHA-256 of a value's canonical JSON form.

    sort_keys so TOML table ordering cannot change the digest; default=str so
    a stray non-JSON scalar degrades to a stable string instead of raising
    inside a security check.
    """
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Grant:
    """One thing the user was asked to approve, and what approving it allows."""

    kind: str    # "config" | "soul"
    key: str     # "mcp_servers.indexer" | ".pyrrhon/team-context.md"
    digest: str  # digest_value of the granted value
    effect: str  # one human line, rendered in the consent prompt

    @property
    def line(self) -> str:
        return f"{self.kind}:{self.key}={self.digest}"


@dataclass(frozen=True)
class TrustFile:
    plugins: frozenset[str]  # legacy bare names
    grants: frozenset[str]   # full "kind:key=digest" lines

    def has(self, grant: Grant) -> bool:
        return grant.line in self.grants


def trust_path(repo_root: Path) -> Path:
    return repo_root / ".pyrrhon" / "trusted"


def read_trust_file(repo_root: Path) -> TrustFile:
    path = trust_path(repo_root)
    if not path.is_file():
        return TrustFile(plugins=frozenset(), grants=frozenset())
    plugins: set[str] = set()
    grants: set[str] = set()
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        # An unreadable trust file means "nothing is granted", never a crash:
        # failing closed is the safe direction and startup must survive it.
        return TrustFile(plugins=frozenset(), grants=frozenset())
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        (grants if ":" in line else plugins).add(line)
    return TrustFile(plugins=frozenset(plugins), grants=frozenset(grants))


def record_grants(repo_root: Path, grants: Iterable[Grant]) -> None:
    """Append grant lines that are not already on record."""
    existing = read_trust_file(repo_root)
    seen: set[str] = set()
    new: list[str] = []
    for grant in grants:
        # De-dupe within this call too: two soul files with identical contents
        # produce different keys, but a caller re-granting the same file twice
        # in one batch would otherwise write the line twice.
        if grant.line in existing.grants or grant.line in seen:
            continue
        seen.add(grant.line)
        new.append(grant.line)
    if not new:
        return
    directory = repo_root / ".pyrrhon"
    directory.mkdir(parents=True, exist_ok=True)
    with trust_path(repo_root).open("a", encoding="utf-8") as handle:
        for line in new:
            handle.write(line + "\n")
