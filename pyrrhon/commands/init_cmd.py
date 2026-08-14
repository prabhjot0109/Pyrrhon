"""/init — scaffold .pyrrhon/soul.md so users can tell Pyrrhon who they are."""

from __future__ import annotations

from pathlib import Path

from pyrrhon.config.trust import record_grants
from pyrrhon.core.agent.soul import soul_grant_for

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
    # Self-grant, for the same reason as the remember tool: /init is the user
    # asking Pyrrhon to write this file, so prompting them to approve Pyrrhon's
    # own template would be consent theatre. Bound to the template's contents,
    # so their first real edit re-prompts — which is correct, since by then the
    # file says something Pyrrhon did not author.
    record_grants(repo_root, [soul_grant_for(repo_root, soul, SOUL_TEMPLATE.strip())])
    return soul, True
