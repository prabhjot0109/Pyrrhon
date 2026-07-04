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
        if not (root / rel).is_file():
            continue  # only surface citations that point at real files
        if (rel, line) in seen:
            continue
        seen.add((rel, line))
        citations.append(Citation(file=rel, line=line))
    return citations
