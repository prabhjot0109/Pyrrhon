"""Find file:line references in agent prose. M1 turns this into a verification gate."""

from __future__ import annotations

import re
from pathlib import Path

from pyrrhon.core.events import Citation

_CITATION_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_][\w./\\-]*\.[A-Za-z0-9_]+):(?P<line>\d+)"
)


def extract_citations(text: str, root: Path) -> list[Citation]:
    seen: set[tuple[str, int]] = set()
    citations: list[Citation] = []
    for match in _CITATION_RE.finditer(text):
        rel = match.group("path").replace("\\", "/")
        line = int(match.group("line"))
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue  # escapes the repo root
        if not candidate.is_file():
            continue
        if (rel, line) in seen:
            continue
        seen.add((rel, line))
        citations.append(Citation(file=rel, line=line))
    return citations


def extract_references(text: str) -> list[tuple[str, int]]:
    """Every path:line match in prose — no existence filtering, no dedupe.

    The grounding gate (gate.py) verifies these mechanically; fabricated
    paths must survive extraction so the gate can catch and strip them.
    extract_citations above keeps its M0 existence-filtered behavior.
    """
    return [
        (match.group("path").replace("\\", "/"), int(match.group("line")))
        for match in _CITATION_RE.finditer(text)
    ]
