"""/init — scaffold .pyrrhon/soul.md so users can tell Pyrrhon who they are."""

from __future__ import annotations

from pathlib import Path

SOUL_TEMPLATE = """\
# Soul

Tell Pyrrhon about yourself. Everything here is loaded into its context at
the start of every session in this repo. Add more .md files (e.g. skill.md)
next to this one — they load too.

## Who I am
<!-- role, experience level, languages you're comfortable in -->

## How I like things explained
<!-- e.g. first principles, short answers, always show the code -->

## Conventions and standards I care about
<!-- naming, architecture rules, style guides -->

## Current goals
<!-- what you're trying to learn or build right now -->
"""


def init_pyrrhon_dir(repo_root: Path) -> tuple[Path, bool]:
    directory = repo_root / ".pyrrhon"
    directory.mkdir(exist_ok=True)
    soul = directory / "soul.md"
    if soul.exists():
        return soul, False
    soul.write_text(SOUL_TEMPLATE, encoding="utf-8")
    return soul, True
