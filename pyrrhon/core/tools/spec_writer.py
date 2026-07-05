"""write_spec — Act 2's artifact writer.

The only tool that writes inside the repo. It may write exactly six spec
artifacts, always under docs/design/. Overwriting is allowed by design: the
conversation is the source of truth and the files are its artifact. The tool
emits no events — the agent's closing SpeechChunk announces what was written.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.tools.base import Tool

SPEC_FILENAMES: tuple[str, ...] = (
    "PRD.md",
    "HLD.md",
    "LLD.md",
    "api.md",
    "database.md",
    "risks.md",
)


class WriteSpecTool(Tool):
    name = "write_spec"
    description = (
        "Write a design spec artifact to docs/design/ in the repo. Only call "
        "this once the design reasoning is explicit — the spec must record "
        "why choices were made, not just what was chosen. Overwriting an "
        "existing artifact is allowed: the conversation is the source of truth."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "enum": list(SPEC_FILENAMES),
                "description": "Which spec artifact to write",
            },
            "content": {
                "type": "string",
                "description": "Full markdown content of the artifact",
            },
        },
        "required": ["filename", "content"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, filename: str, content: str) -> str:
        if filename not in SPEC_FILENAMES:
            return (
                f"ERROR: '{filename}' is not an allowed spec artifact. "
                f"Allowed filenames: {', '.join(SPEC_FILENAMES)}."
            )
        return await asyncio.to_thread(self._write, filename, content)

    def _write(self, filename: str, content: str) -> str:
        directory = self.root / "docs" / "design"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        verb = "Overwrote" if existed else "Wrote"
        return f"{verb} docs/design/{filename} ({len(content)} characters)."
