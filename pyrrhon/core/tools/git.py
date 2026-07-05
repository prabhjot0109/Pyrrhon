"""Git history tools: log, blame, show.

Ordinary tools for history-aware questions — explicitly NOT part of citation
verification (spec amendment 2026-07-03). All subprocess calls use
asyncio.create_subprocess_exec (argv list, cwd=root); never shell=True, so no
quoting/injection surface. Subprocess awaits are natural cancellation points
for M3's abort_current_turn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.repo import _resolve_inside

MAX_GIT_OUTPUT = 8000


async def _run_git(root: Path, *args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as exc:
        return f"ERROR: could not run git: {exc}"
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        return f"ERROR: {detail or f'git exited with code {proc.returncode}'}"
    text = stdout.decode("utf-8", errors="replace")
    if len(text) > MAX_GIT_OUTPUT:
        text = text[:MAX_GIT_OUTPUT] + "\n(truncated)"
    return text or "(no output)"


class GitLogTool(Tool):
    name = "git_log"
    description = (
        "Show recent commit history (short hash, date, author, subject), most "
        "recent first — optionally filtered to one file or directory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional repo-relative path to filter history",
            },
            "max_count": {
                "type": "integer",
                "description": "How many commits to show (default 20, max 100)",
            },
        },
        "required": [],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, path: str | None = None, max_count: int = 20) -> str:
        max_count = max(1, min(int(max_count), 100))
        args = [
            "log",
            f"--max-count={max_count}",
            "--date=short",
            "--format=%h %ad %an %s",
        ]
        if path:
            if _resolve_inside(self.root, path) is None:
                return f"ERROR: '{path}' is outside the repo."
            args += ["--", path]
        return await _run_git(self.root, *args)


class GitBlameTool(Tool):
    name = "git_blame"
    description = (
        "Show who last changed each line of a file (and when). Use start_line/"
        "end_line to blame just a range."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path"},
            "start_line": {"type": "integer", "description": "1-based first line"},
            "end_line": {
                "type": "integer",
                "description": "1-based last line, inclusive (defaults to start_line)",
            },
        },
        "required": ["path"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(
        self, path: str, start_line: int | None = None, end_line: int | None = None
    ) -> str:
        if _resolve_inside(self.root, path) is None:
            return f"ERROR: '{path}' is outside the repo."
        args = ["blame", "--date=short"]
        if start_line is not None:
            args += ["-L", f"{start_line},{end_line or start_line}"]
        args += ["--", path]
        return await _run_git(self.root, *args)


class GitShowTool(Tool):
    name = "git_show"
    description = (
        "Show a commit: message, author, and full diff. Pass a ref like a short "
        "hash from git_log, or HEAD, or HEAD~2."
    )
    parameters = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "Commit ref, e.g. 'a1b2c3d' or 'HEAD~1'"},
        },
        "required": ["ref"],
    }

    def __init__(self, root: Path):
        self.root = root

    async def run(self, ref: str) -> str:
        ref = ref.strip()
        if not ref or ref.startswith("-"):
            return f"ERROR: invalid ref '{ref}'."
        return await _run_git(self.root, "show", ref)
