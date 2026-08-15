"""Per-turn record of what the model was actually shown.

The gate proves a cited line is IN RANGE of a real file. It cannot prove the
model ever looked at that line — and `repo_map` hands it a list of real paths
with real line numbers, so inventing a plausible in-range citation costs the
model nothing and passes every check. That is the gap VISION.md cares about
when it asks for a *correct* file:line, not merely an existing one.

Evidence is recorded as RANGES, not points. A model that reads lines 1-400 of
a file and then cites line 37 is citing something it genuinely saw; requiring
an exact match would punish correct behaviour and make Pyrrhon sound unsure
about work it actually did.

Parsed from the tool OUTPUT rather than the arguments wherever possible: the
output is what the model was shown, and the arguments are only what it asked
for (read_file clamps to MAX_READ_LINES, grep truncates at MAX_GREP_MATCHES).
"""

from __future__ import annotations

import re
from typing import Any

from pyrrhon.core.grounding.citations import extract_references

# ReadFileTool renders "    12| source text". The line number it prints is the
# authoritative record of what was displayed.
_NUMBERED = re.compile(r"^\s*(\d+)\|", re.MULTILINE)

# A bare path token, for tools whose output lists files without line numbers.
# extract_references cannot serve here: it requires "path:<digits>", and
# repo_map's header line is "pyrrhon/core/session.py:" with the line numbers
# on the indented symbol rows beneath it.
_PATH_TOKEN = re.compile(r"(?<![\w./\\-])([A-Za-z0-9_][\w./\\-]*\.[A-Za-z0-9_]+)")

# Tools whose output carries no line evidence at all. repo_map and glob prove a
# file EXISTS; they show no source, so they can never license a line citation.
# list_dependencies is the same: it names import edges, not locations.
_FILE_ONLY = {"repo_map", "glob", "list_dependencies"}

# git blame's output format is not path:line, so its range comes from the
# arguments — which for blame are exact, because -L is passed through verbatim.
_RANGE_FROM_ARGS = {"git_blame"}

# Stands in for "to the end of the file" when git blame was given no -L range.
# The gate has already bounded the line against the real line count by the time
# observed() is consulted, so an open upper bound here cannot verify a line
# that does not exist.
_WHOLE_FILE = 10**9


def _normalise(rel: str) -> str:
    """Repo-relative key, matched to how citations.py spells the same path.

    The citation regex starts at [A-Za-z0-9_], so it reports ".pyrrhon/x.toml"
    as "pyrrhon/x.toml" and "./app.py" as "app.py". Stripping the same leading
    characters here is what makes a ledger key and a gate lookup agree.
    """
    return rel.replace("\\", "/").lstrip("./")


class EvidenceLedger:
    """Observed line ranges per repo-relative file, for one turn."""

    def __init__(self) -> None:
        self._ranges: dict[str, list[tuple[int, int]]] = {}
        self.files: set[str] = set()

    def record_range(self, rel: str, start: int, end: int) -> None:
        if start > end:
            start, end = end, start
        key = _normalise(rel)
        self.files.add(key)
        self._ranges.setdefault(key, []).append((start, end))

    def record_line(self, rel: str, line: int) -> None:
        self.record_range(rel, line, line)

    def record_file(self, rel: str) -> None:
        """Existence only — no line inside it becomes citable."""
        self.files.add(_normalise(rel))

    def observed(self, rel: str, line: int) -> bool:
        """Did some tool result this turn display `line` of `rel`?

        O(ranges-for-that-file), no I/O. This runs once per reference on the
        speech critical path, where the whole gate check budgets ~0.025ms.
        """
        return any(
            start <= line <= end for start, end in self._ranges.get(_normalise(rel), ())
        )

    def record_tool_result(self, name: str, args: Any, result: Any) -> None:
        """Fold one tool result into the ledger.

        Never raises: a malformed or unrecognised result simply contributes no
        evidence, which fails closed — the citation gets hedged rather than
        the turn dying on the speech path.
        """
        if not isinstance(result, str) or result.startswith("ERROR:"):
            return
        args = args if isinstance(args, dict) else {}
        path = args.get("path")

        if name in _FILE_ONLY:
            for match in _PATH_TOKEN.finditer(result):
                self.record_file(match.group(1))
            return

        if name in _RANGE_FROM_ARGS and isinstance(path, str):
            self._record_blame(path, args)
            return

        # symbol_context prints a numbered window of the DEFINING file, whose
        # path is not in the arguments — the tool discovers it, so it is the
        # first path:line in the output. Handled separately so the window's
        # lines count as observed, not just the header line. The call sites it
        # lists are points, not ranges: their source was never displayed.
        if name == "symbol_context":
            references = extract_references(result)
            if references:
                defining = references[0][0]
                numbered = [int(n) for n in _NUMBERED.findall(result)]
                if numbered:
                    self.record_range(defining, min(numbered), max(numbered))
            for rel, line in references:
                self.record_line(rel, line)
            return

        # read_file: the numbered gutter is the exact record of what was shown.
        numbered = [int(n) for n in _NUMBERED.findall(result)]
        if isinstance(path, str) and numbered:
            self.record_range(path, min(numbered), max(numbered))

        # grep / find_symbol / find_references / MCP tools: every "path:line"
        # in the output is a line the model was shown.
        for rel, line in extract_references(result):
            self.record_line(rel, line)

    def _record_blame(self, path: str, args: dict) -> None:
        start, end = args.get("start_line"), args.get("end_line")
        try:
            first = int(start) if start is not None else None
            last = int(end) if end is not None else None
        except (TypeError, ValueError):
            return  # unparseable range: record nothing rather than guess
        if first is None:
            self.record_range(path, 1, _WHOLE_FILE)  # blamed the whole file
        else:
            # GitBlameTool defaults end_line to start_line, same as -L does.
            self.record_range(path, first, last if last is not None else first)
