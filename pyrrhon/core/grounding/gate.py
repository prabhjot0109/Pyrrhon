"""The grounding gate: mechanical verification of file:line claims.

Runs between the LLM's final text and the output channels — nothing reaches
the speakers (or the screen) carrying a reference this gate could not verify.
Verification is file:line only: the file exists inside the repo and the line
number is within its line count (spec "Grounding gate", amended 2026-07-03).
Unverifiable references are stripped from the speakable text and replaced
with a single honest hedge sentence.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_references

HEDGE = "I couldn't verify that location."


@dataclass(frozen=True)
class GroundedText:
    speech_text: str
    citations: tuple[Citation, ...]
    unverified: tuple[str, ...]


class GroundingGate:
    def __init__(self, root: Path):
        self.root = root

    async def check(self, text: str) -> GroundedText:
        # Real-time discipline: every file read happens off the event loop.
        return await asyncio.to_thread(self._check_sync, text)

    def _check_sync(self, text: str) -> GroundedText:
        line_counts: dict[str, int | None] = {}
        verified: list[Citation] = []
        unverified: list[str] = []
        seen_ok: set[tuple[str, int]] = set()
        seen_bad: set[str] = set()

        for rel, line in extract_references(text):
            if rel not in line_counts:
                line_counts[rel] = self._count_lines(rel)
            count = line_counts[rel]
            if count is not None and 1 <= line <= count:
                if (rel, line) not in seen_ok:
                    seen_ok.add((rel, line))
                    verified.append(Citation(file=rel, line=line))
            else:
                ref = f"{rel}:{line}"
                if ref not in seen_bad:
                    seen_bad.add(ref)
                    unverified.append(ref)

        speech = text
        if unverified:
            for ref in unverified:
                rel, _, line_str = ref.rpartition(":")
                # Match both the normalized (/) and original (\) spellings;
                # \b after the line number keeps app.py:5 from eating app.py:55.
                pattern = re.compile(
                    re.escape(rel).replace("/", r"[/\\]") + ":" + line_str + r"\b"
                )
                speech = pattern.sub("", speech)
            speech = re.sub(r"[ \t]{2,}", " ", speech).strip()
            speech = f"{speech} {HEDGE}" if speech else HEDGE

        return GroundedText(
            speech_text=speech,
            citations=tuple(verified),
            unverified=tuple(unverified),
        )

    def _count_lines(self, rel: str) -> int | None:
        """Line count of a repo file, or None if missing/unreadable/escaping."""
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            return None  # ../-style escape — never verify outside the repo
        if not target.is_file():
            return None
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return len(content.splitlines())
