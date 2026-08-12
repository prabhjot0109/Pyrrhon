"""Soul files: user-authored markdown loaded into the system prompt each session.

Users create them with /init (or by hand) in ~/.pyrrhon/ and <repo>/.pyrrhon/.
Global loads first, repo last — so repo-level context wins.

The total is capped. Everything here is re-sent on every round of every turn,
and memory.md alone holds up to MAX_MEMORY_BULLETS entries: a full one pushes
the system prompt past 26 KB (~6.6k tokens), which is several times the cost
of the entire tool belt and is paid again on each tool round. The cap is a
character budget rather than a file count so one runaway file cannot crowd out
the rest.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pyrrhon.core.agent.prompts import SYSTEM_PROMPT

log = logging.getLogger("pyrrhon.soul")

# ~1.5k tokens. Enough for real standing context, small enough that it is not
# the dominant line item in a per-round prompt.
MAX_SOUL_CHARS = 6000

_TRIM_NOTE = "…[trimmed to keep the system prompt small]"


def _keep_newest(content: str, budget: int) -> str:
    """Trim from the FRONT, keeping the most recent lines.

    For memory.md specifically. RememberTool appends, so the end of the file
    is the recent end; trimming the tail — the natural default for prose —
    would discard exactly the entries most likely to still matter.
    """
    lines = content.splitlines()
    kept: list[str] = []
    used = len(_TRIM_NOTE) + 1
    for line in reversed(lines):
        used += len(line) + 1
        if used > budget:
            break
        kept.append(line)
    kept.reverse()
    return "\n".join([_TRIM_NOTE, *kept])


def _soul_files(repo_root: Path, home: Path) -> list[tuple[Path, str]]:
    """Every readable soul file, in load order (global first, repo last)."""
    found: list[tuple[Path, str]] = []
    for directory in (home / ".pyrrhon", repo_root / ".pyrrhon"):
        if not directory.is_dir():
            continue
        for md in sorted(directory.glob("*.md")):
            try:
                content = md.read_text(encoding="utf-8").strip()
            except OSError:  # unreadable file must not break startup
                continue
            if content:
                found.append((md, content))
    return found


def load_soul(
    repo_root: Path, home: Path | None = None, max_chars: int = MAX_SOUL_CHARS
) -> str:
    home = home or Path.home()
    files = _soul_files(repo_root, home)

    # Budget is claimed in REVERSE load order — repo files before global ones —
    # because repo context wins. Allocating forward would let a large global
    # file starve the repo's own notes, silently inverting that precedence.
    # The rendered order stays the original one.
    remaining = max_chars
    trimmed: dict[Path, str] = {}
    for path, content in reversed(files):
        if remaining <= 0:
            continue  # budget exhausted: this file is dropped entirely
        if len(content) > remaining:
            content = (
                _keep_newest(content, remaining)
                if path.name == "memory.md"
                else content[:remaining].rstrip() + "\n" + _TRIM_NOTE
            )
        trimmed[path] = content
        remaining -= len(content)

    total = sum(len(content) for _, content in files)
    if total > max_chars:
        log.info(
            "soul files trimmed from %d to %d chars to keep the per-round "
            "prompt small; memory.md keeps its newest entries",
            total,
            max_chars,
        )

    return "\n\n".join(
        f"## From {path.name}\n\n{trimmed[path]}"
        for path, _ in files
        if path in trimmed
    )


def build_system_prompt(repo_root: Path, home: Path | None = None) -> str:
    prompt = SYSTEM_PROMPT + f"\nThe repo under discussion is rooted at: {repo_root}\n"
    soul = load_soul(repo_root, home)
    if soul:
        prompt += f"\n# User context (soul files)\n\n{soul}\n"
    return prompt
