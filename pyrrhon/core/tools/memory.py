"""The remember tool: session memory in <repo>/.pyrrhon/memory.md.

The agent calls it when something is worth carrying across sessions —
decisions made, corrections the user gave, repo quirks discovered. Reading
is free: memory.md sits in .pyrrhon/, so the soul loader already ingests it
at session start. The user may edit or prune the file freely (spec "Session
memory: memory.md", added 2026-07-03).

The file is kept bounded: an exact-duplicate fact is dropped, and the bullet
count is capped (oldest bullets fall off) so memory.md can't grow without
limit and bloat the system prompt — which was pushing long sessions toward the
context window. Capping is deterministic (no LLM), so a `remember` mid-turn
stays instant; hand-written prose between bullets is preserved.

Real-time discipline: the file write is offloaded via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import datetime
import re
from pathlib import Path

from pyrrhon.core.tools.base import Tool

MEMORY_HEADER = "# Memory\n"
MAX_MEMORY_BULLETS = 200  # oldest bullets fall off beyond this; generous cap

# A bullet is "- [date] fact" (date optional); capture the fact text for dedup.
_BULLET_FACT = re.compile(r"^- (?:\[[^\]]*\]\s*)?(.*)$")


def _fact_text(bullet_line: str) -> str:
    match = _BULLET_FACT.match(bullet_line)
    return " ".join(match.group(1).split()) if match else ""


class RememberTool(Tool):
    name = "remember"
    description = (
        "Save a key fact worth keeping across sessions (a decision, a user "
        "correction, a repo quirk). Appends a dated bullet to .pyrrhon/memory.md."
    )
    parameters = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "One self-contained sentence to remember",
            },
        },
        "required": ["fact"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, fact: str) -> str:
        return await asyncio.to_thread(self._append, fact)

    def _append(self, fact: str) -> str:
        fact = " ".join(fact.split())  # one bullet per fact — no embedded newlines
        if not fact:
            return "ERROR: nothing to remember (empty fact)."
        directory = self.root / ".pyrrhon"
        try:
            directory.mkdir(exist_ok=True)
            memory = directory / "memory.md"
            existing = (
                memory.read_text(encoding="utf-8") if memory.exists() else MEMORY_HEADER
            )
            lines = existing.splitlines()
            if fact in {_fact_text(ln) for ln in lines if ln.startswith("- ")}:
                return f"Already in memory: {fact}"
            stamp = datetime.date.today().isoformat()
            lines.append(f"- [{stamp}] {fact}")
            lines = _cap_bullets(lines, MAX_MEMORY_BULLETS)
            memory.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            return f"ERROR: could not write memory.md: {exc}"
        return f"Remembered: {fact}"


def _cap_bullets(lines: list[str], cap: int) -> list[str]:
    """Drop the oldest bullet lines so at most `cap` remain, leaving every
    non-bullet line (header, user prose) untouched and in place."""
    bullet_idx = [i for i, ln in enumerate(lines) if ln.startswith("- ")]
    if len(bullet_idx) <= cap:
        return lines
    drop = set(bullet_idx[: len(bullet_idx) - cap])
    return [ln for i, ln in enumerate(lines) if i not in drop]
