"""Read-only repo tools, sandboxed to the repo root.

Real-time discipline: `run()` methods do no filesystem work on the event
loop — the sync body is offloaded via asyncio.to_thread(), because in M3 a
~100ms loop stall becomes an audible audio glitch.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pyrrhon.core.tools.base import Tool

SKIP_DIRS = {".git", ".pyrrhon", ".venv", "node_modules", "__pycache__"}
MAX_GREP_MATCHES = 50
MAX_GLOB_MATCHES = 100
MAX_READ_LINES = 400


def _resolve_inside(root: Path, rel: str) -> Path | None:
    """Resolve `rel` against root; None if it escapes the repo (e.g. '../')."""
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file from the repo. Returns numbered lines so claims can be "
        "cited as path:line."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path"},
            "start_line": {"type": "integer", "description": "1-based first line"},
            "end_line": {"type": "integer", "description": "1-based last line, inclusive"},
        },
        "required": ["path"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        return await asyncio.to_thread(self._read, path, start_line, end_line)

    def _read(self, path: str, start_line: int, end_line: int | None) -> str:
        target = _resolve_inside(self.root, path)
        if target is None:
            return f"ERROR: '{path}' is outside the repo."
        if not target.is_file():
            return f"ERROR: '{path}' does not exist."
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(start_line, 1)
        last = min(end_line or len(lines), first - 1 + MAX_READ_LINES, len(lines))
        numbered = [f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1)]
        return "\n".join(numbered) or f"(no lines in range for {path})"


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a Python regex. Returns 'path:line: text'."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression"},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, pattern: str) -> str:
        return await asyncio.to_thread(self._search, pattern)

    def _search(self, pattern: str) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"ERROR: bad regex: {exc}"
        hits: list[str] = []
        for path in _iter_files(self.root):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable — skip
            rel = path.relative_to(self.root).as_posix()
            for n, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()}")
                    if len(hits) >= MAX_GREP_MATCHES:
                        return "\n".join(hits) + "\n(truncated)"
        return "\n".join(hits) or "No matches."


class GlobTool(Tool):
    name = "glob"
    description = "List repo files matching a glob pattern like '**/*.py'."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'"},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, pattern: str) -> str:
        return await asyncio.to_thread(self._match, pattern)

    def _match(self, pattern: str) -> str:
        # Reject absolute patterns; glob patterns must be repo-relative
        if Path(pattern).is_absolute():
            return f"ERROR: glob pattern must be repo-relative (got '{pattern}')."
        try:
            matches = [
                p.relative_to(self.root).as_posix()
                for p in sorted(self.root.glob(pattern))
                if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
            ]
        except (ValueError, OSError, NotImplementedError):
            return f"ERROR: Invalid glob pattern '{pattern}'."
        return "\n".join(matches[:MAX_GLOB_MATCHES]) or "No files match."
