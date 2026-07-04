"""The remember tool: append-only session memory in <repo>/.pyrrhon/memory.md.

The agent calls it when something is worth carrying across sessions —
decisions made, corrections the user gave, repo quirks discovered. Reading
is free: memory.md sits in .pyrrhon/, so the soul loader already ingests it
at session start. The user may edit or prune the file freely; this tool only
ever appends (spec "Session memory: memory.md", added 2026-07-03).

Real-time discipline: the file write is offloaded via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

from pyrrhon.core.tools.base import Tool

MEMORY_HEADER = "# Memory\n"


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
            if not memory.exists():
                memory.write_text(MEMORY_HEADER, encoding="utf-8")
            stamp = datetime.date.today().isoformat()
            with memory.open("a", encoding="utf-8") as f:
                f.write(f"- [{stamp}] {fact}\n")
        except OSError as exc:
            return f"ERROR: could not write memory.md: {exc}"
        return f"Remembered: {fact}"
