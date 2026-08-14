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

from pyrrhon.config.trust import Grant, digest_value, read_trust_file
from pyrrhon.core.agent.prompts import SYSTEM_PROMPT

log = logging.getLogger("pyrrhon.soul")

SOUL_EFFECT = "write into Pyrrhon's own instructions"

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


def _readable_markdown(directory: Path) -> list[tuple[Path, str]]:
    """Non-empty .md files in one directory, sorted, skipping unreadable ones
    (an unreadable file must never break startup)."""
    if not directory.is_dir():
        return []
    found: list[tuple[Path, str]] = []
    for md in sorted(directory.glob("*.md")):
        try:
            content = md.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if content:
            found.append((md, content))
    return found


def soul_grant_for(repo_root: Path, path: Path, content: str) -> Grant:
    """The grant that would authorise this repo file at these exact contents.

    Public because the two places Pyrrhon *writes* a soul file — /init and the
    remember tool — self-grant what they just wrote. Without that, the gate
    below would hide the user's own memory and prompt them to approve words
    they dictated a second ago.
    """
    rel = path.relative_to(repo_root).as_posix()
    return Grant("soul", rel, digest_value(content), f"{SOUL_EFFECT}: {rel}")


def pending_soul_grants(repo_root: Path) -> list[Grant]:
    """Repo soul files the user has not approved at their current contents."""
    trust = read_trust_file(repo_root)
    return [
        grant
        for md, content in _readable_markdown(repo_root / ".pyrrhon")
        if not trust.has(grant := soul_grant_for(repo_root, md, content))
    ]


def _soul_files(repo_root: Path, home: Path) -> list[tuple[Path, str]]:
    """Every soul file we are allowed to load, in order (global first).

    Global files are the user's own and load unconditionally. Repo files
    arrived with the clone, so each needs a grant bound to its current
    contents — editing a granted file revokes the grant, which is the point.
    A repo .md is not passive data: it is appended to the system prompt, so an
    ungated one can instruct Pyrrhon to stop citing sources, defeating the
    grounding gate from inside the prompt rather than around it.
    """
    found = _readable_markdown(home / ".pyrrhon")
    repo_dir = repo_root / ".pyrrhon"
    if repo_dir.resolve() == (home / ".pyrrhon").resolve():
        # Pyrrhon pointed at the user's own home: the "repo" files ARE the
        # global ones, already loaded above. Scanning again would duplicate
        # every file into the prompt.
        return found
    trust = read_trust_file(repo_root)
    for md, content in _readable_markdown(repo_dir):
        if trust.has(soul_grant_for(repo_root, md, content)):
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
