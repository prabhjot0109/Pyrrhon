"""Read-only repo tools, sandboxed to the repo root.

Real-time discipline: `run()` methods do no filesystem work on the event
loop — the sync body is offloaded via asyncio.to_thread(), because in M3 a
~100ms loop stall becomes an audible audio glitch.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from fnmatch import fnmatch
from pathlib import Path

from pyrrhon.core.tools.base import Tool

SKIP_DIRS = {".git", ".pyrrhon", ".venv", "node_modules", "__pycache__"}
MAX_GREP_MATCHES = 50
MAX_GLOB_MATCHES = 100
MAX_READ_LINES = 400

# Sentinel distinct from None, which is the legitimate "rg is not installed"
# answer and must be cached rather than re-probed on every grep.
_RG_PATH: str | None = None
_RG_RESOLVED = False


def _resolve_inside(root: Path, rel: str) -> Path | None:
    """Resolve `rel` against root; None if it escapes the repo (e.g. '../')."""
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _iter_files(root: Path):
    """Yield repo files in sorted order, pruning SKIP_DIRS at the directory
    level with os.scandir so huge ignored trees (.venv, node_modules) are never
    descended into. Same result set as the old sorted(rglob('*')) filter, but
    the sort is now over the pruned set — not every file in the tree — which is
    the grep-latency win on large repos. Order stays deterministic (stable
    truncation at MAX_GREP_MATCHES).

    Sorted by the POSIX path STRING, not by Path. Path.__lt__ case-folds on
    Windows, so it would order 'docs/' before 'README.md' while ripgrep's
    --sort=path uses byte order and does the opposite. Since grep truncates at
    MAX_GREP_MATCHES, a different order means a different set of results — so
    the fallback has to sort the way rg does or the two paths disagree."""
    files: list[Path] = []
    stack = [str(root)]
    while stack:
        try:
            scan = os.scandir(stack.pop())
        except OSError:
            continue  # unreadable dir: skip, don't abort the whole walk
        with scan:
            for entry in scan:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_DIRS:
                        stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(Path(entry.path))
    yield from sorted(files, key=lambda p: p.as_posix())


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


def _ripgrep() -> str | None:
    """Absolute path to rg, or None. Resolved once — shutil.which walks PATH."""
    global _RG_PATH, _RG_RESOLVED
    if not _RG_RESOLVED:
        _RG_PATH = shutil.which("rg")
        _RG_RESOLVED = True
    return _RG_PATH


def _format_hit(rel: str, line_no: str, text: str, is_match: bool) -> str:
    """One output line. Matches keep the historic 'path:line: text' shape —
    the model cites from it and the grounding gate parses it."""
    sep = ":" if is_match else "-"
    return f"{rel}:{line_no}{sep} {text.strip()}"


class GrepTool(Tool):
    """Content search, backed by ripgrep when it is installed.

    The pure-Python scan below reads every file in the repo into Python and
    regexes it line by line, per call, uncached — seconds on a large repo,
    directly in front of an answer. rg does the same work in milliseconds.

    Subprocess use here is deliberate and fenced: argv list via
    create_subprocess_exec (never a shell), cwd pinned to the repo root, and
    the user-supplied pattern only ever appears AFTER a literal `--`, so a
    pattern beginning with `-` can never be read as a flag. This is the same
    pattern git.py already uses; tests/test_safety.py allowlists both modules
    and asserts the argv shape.

    The fallback is kept, and both paths are held to the SAME semantics on
    purpose: rg defaults to honouring .gitignore and skipping hidden files,
    which would mean search results silently depended on whether rg happened
    to be installed. --no-ignore --hidden plus explicit SKIP_DIRS excludes
    restores parity, and --sort=path makes truncation deterministic (rg is
    multi-threaded and otherwise emits in completion order).
    """

    name = "grep"
    description = (
        "Search file contents with a regex. Returns 'path:line: text'. Narrow "
        "with path (a subdirectory or file) and glob (e.g. '*.py'); use "
        "context_lines to see surrounding lines without a separate read_file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression"},
            "path": {
                "type": "string",
                "description": "Optional repo-relative directory or file to search in",
            },
            "glob": {
                "type": "string",
                "description": "Optional filename filter, e.g. '*.py' or '*.{ts,tsx}'",
            },
            "ignore_case": {"type": "boolean", "description": "Case-insensitive search"},
            "context_lines": {
                "type": "integer",
                "description": "Lines of context around each match (0-10)",
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path):
        self.root = root
        # _resolve_inside hands back resolved absolute paths, so the root has
        # to be resolved too or relative_to() raises for every file.
        self._root = root.resolve()

    async def run(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        ignore_case: bool = False,
        context_lines: int = 0,
    ) -> str:
        if not pattern:
            return "ERROR: grep needs a pattern."
        context_lines = max(0, min(int(context_lines or 0), 10))
        target = self._root
        if path:
            resolved = _resolve_inside(self.root, path)
            if resolved is None:
                return f"ERROR: '{path}' is outside the repo."
            if not resolved.exists():
                return f"ERROR: '{path}' does not exist."
            target = resolved
        rg = _ripgrep()
        if rg:
            return await self._search_rg(
                rg, pattern, target, glob, ignore_case, context_lines
            )
        return await asyncio.to_thread(
            self._search, pattern, target, glob, ignore_case, context_lines
        )

    def _rg_argv(
        self,
        rg: str,
        pattern: str,
        target: Path,
        glob: str | None,
        ignore_case: bool,
        context_lines: int,
    ) -> list[str]:
        argv = [
            rg,
            # Structured output, not text. rg's text form is 'path:line:text'
            # for matches and 'path-line-text' for context, which is genuinely
            # ambiguous: a filename containing '-' (or ':') makes the
            # separator scan guess wrong. JSON records carry the path, line
            # number and match/context kind as separate fields.
            "--json",
            # Parity with the Python fallback, which walks everything except
            # SKIP_DIRS and does not consult .gitignore.
            "--no-ignore",
            "--hidden",
            # Deterministic order, so truncation at MAX_GREP_MATCHES is stable.
            "--sort=path",
        ]
        for skip in sorted(SKIP_DIRS):
            argv += ["--glob", f"!{skip}/"]
        if glob:
            argv += ["--glob", glob]
        if ignore_case:
            argv.append("--ignore-case")
        if context_lines:
            argv += ["--context", str(context_lines)]
        # Everything after `--` is data. The pattern can now safely start with
        # a dash without rg parsing it as an option.
        argv += ["--", pattern]
        # Search path is given RELATIVE to cwd (= the repo root), because rg
        # echoes back whatever form it was handed. Passing an absolute path
        # would put absolute paths in every citation. When the target is the
        # root itself the argument is omitted entirely — `rg -- pat` searches
        # cwd and emits bare relative paths, whereas `rg -- pat .` prefixes
        # every one with "./".
        if target != self._root:
            argv.append(target.relative_to(self._root).as_posix())
        return argv

    async def _search_rg(
        self,
        rg: str,
        pattern: str,
        target: Path,
        glob: str | None,
        ignore_case: bool,
        context_lines: int,
    ) -> str:
        argv = self._rg_argv(rg, pattern, target, glob, ignore_case, context_lines)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # A JSON record embeds the whole source line; the 64KB default
                # raises on minified files.
                limit=1 << 20,
            )
        except OSError:
            # rg vanished between which() and exec: fall back rather than fail.
            return await asyncio.to_thread(
                self._search, pattern, target, glob, ignore_case, context_lines
            )

        # Both pipes were requested above, so neither is None. asyncio types
        # them Optional because the same call also serves DEVNULL/inherit.
        assert proc.stdout is not None and proc.stderr is not None

        hits: list[str] = []
        matches = 0
        capped = False
        try:
            # Read incrementally and stop the search the moment the cap is
            # reached. Without this rg scans the entire tree even for a common
            # pattern, while the Python fallback exits at the 50th match —
            # measured at 13k files, that alone made rg the slower of the two.
            async for record_line in proc.stdout:
                parsed = self._parse_rg_record(record_line)
                if parsed is None:
                    continue
                rel, line_no, text, is_match = parsed
                if is_match:
                    matches += 1
                    if matches > MAX_GREP_MATCHES:
                        capped = True
                        break
                hits.append(_format_hit(rel, line_no, text, is_match))
        except (ValueError, asyncio.LimitOverrunError):
            capped = True  # pathological line length: keep what we have
        finally:
            # Kill ONLY when we walked away early; then reap unconditionally.
            # returncode stays None until the child is waited on, so the old
            # `if returncode is None: kill()` fired on a clean EOF too and the
            # branch below read a kill signal as a failed search.
            if capped and proc.returncode is None:
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()

        if capped:
            hits.append("(truncated)")
            return "\n".join(hits)

        stderr = await proc.stderr.read()
        if proc.returncode == 1:
            return "No matches."  # rg's documented "no matches" exit code
        if proc.returncode not in (0, 1):
            detail = stderr.decode("utf-8", errors="replace").strip()
            return f"ERROR: bad regex: {detail}" if detail else "ERROR: grep failed."
        return "\n".join(hits) or "No matches."

    def _parse_rg_record(self, record_line: bytes) -> tuple[str, str, str, bool] | None:
        """One rg --json record -> (rel_path, line_no, text, is_match).

        Only "match" and "context" records carry content; begin/end/summary
        are skipped. Paths are normalised to POSIX so citations read the same
        on every platform.
        """
        try:
            record = json.loads(record_line)
        except ValueError:
            return None
        kind = record.get("type")
        if kind not in ("match", "context"):
            return None
        data = record.get("data", {})
        rel = (data.get("path") or {}).get("text")
        line_no = data.get("line_number")
        if rel is None or line_no is None:
            return None  # non-UTF8 path or binary hit: rg sends bytes instead
        return (
            rel.replace("\\", "/"),
            str(line_no),
            (data.get("lines") or {}).get("text", ""),
            kind == "match",
        )

    def _search(
        self,
        pattern: str,
        target: Path,
        glob: str | None,
        ignore_case: bool,
        context_lines: int,
    ) -> str:
        """Pure-Python fallback. Same semantics as the rg path by design."""
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return f"ERROR: bad regex: {exc}"
        hits: list[str] = []
        matches = 0
        files = [target] if target.is_file() else _iter_files(target)
        for path in files:
            try:
                rel = path.relative_to(self._root).as_posix()
            except ValueError:
                continue
            if glob and not (fnmatch(rel, glob) or fnmatch(path.name, glob)):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable — skip
            lines = text.splitlines()
            match_lines = [n for n, line in enumerate(lines, start=1) if rx.search(line)]
            if not match_lines:
                continue
            # Resolve match/context status against ALL matches in the file
            # before emitting. Deciding per-window instead would mislabel a
            # line that is itself a match but happens to fall inside an
            # earlier match's context window, and would emit it twice when
            # windows overlap. rg does neither.
            is_match = set(match_lines)
            emitted: set[int] = set()
            for n in match_lines:
                matches += 1
                if matches > MAX_GREP_MATCHES:
                    hits.append("(truncated)")
                    return "\n".join(hits)
                first = max(1, n - context_lines)
                last = min(len(lines), n + context_lines)
                for m in range(first, last + 1):
                    if m in emitted:
                        continue
                    emitted.add(m)
                    hits.append(_format_hit(rel, str(m), lines[m - 1], m in is_match))
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
