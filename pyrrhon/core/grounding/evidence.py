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

# Tools whose output is a SUBAGENT's prose. Their path:line citations are
# provenance already, absorbed from the subagent's own ledger with a real tool
# result behind each one — so mining the prose as well would add nothing true
# and would license every location the subagent merely GUESSED. Same reasoning
# as read_image's branch below, and the same conclusion.
_REPORTED = {"explore", "think_deeper"}

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
        # Ranges a SUBAGENT verified with its own tools and reported back.
        # A second bucket rather than the same one, because the ledger has two
        # consumers asking opposite questions of it. The gate asks "did we
        # verify this line", and a subagent opening a line IS Pyrrhon opening
        # it — its own LINE_UNSEEN_HEDGE says "this session", not "this
        # context". M16c's re-read suppression asks "is this line already in
        # the model's context", and the answer there is no: the parent was
        # handed a report, not the source. Merging the two would make the
        # parent skip a read for lines it was never shown, which is the exact
        # hazard bootstrap.py gives each subagent its own read_file to avoid.
        self._elsewhere: dict[str, list[tuple[int, int]]] = {}
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
        key = _normalise(rel)
        return any(
            start <= line <= end
            for start, end in (*self._ranges.get(key, ()), *self._elsewhere.get(key, ()))
        )

    def absorb(self, other: "EvidenceLedger") -> None:
        """Take a subagent's evidence as VERIFIED, not as displayed.

        The firewall's cost, if this did not exist: the subagent verifies
        loop.py:431 with its own read_file and reports it, the parent's ledger
        never saw that read, and the gate downgrades or strips a citation that
        WAS verified. A firewall that makes grounding worse is a regression
        whatever it saves.

        Everything the other ledger holds lands in `_elsewhere`, including its
        own `_elsewhere` — a report relayed once is still a line some tool
        actually displayed, and depth is 1 so the chain cannot grow.
        """
        for source in (other._ranges, other._elsewhere):
            for rel, ranges in source.items():
                self._elsewhere.setdefault(rel, []).extend(ranges)
        self.files |= other.files

    def covered(self, rel: str) -> list[tuple[int, int]]:
        """Merged line ranges DISPLAYED for `rel` this turn, ascending.

        `_elsewhere` is deliberately absent. A caller of this is deciding
        whether to spend a round fetching bytes that are already in context,
        and a line a subagent read behind the firewall is not in context —
        suppressing that read would leave the model citing a report it cannot
        check against source it never received.

        `observed` answers "was this one line shown"; this answers "which of
        these lines were", which is the question a re-read has to ask before
        it spends a round fetching bytes already in context. Merged so a
        caller can trim a requested window against it without re-deriving the
        overlaps, and adjacent ranges join because 1-200 followed by 201-400
        is one span to anything that reads it.
        """
        merged: list[tuple[int, int]] = []
        for start, end in sorted(self._ranges.get(_normalise(rel), ())):
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def fingerprint(self) -> tuple[frozenset[str], frozenset[tuple[str, int, int]]]:
        """A comparable snapshot of everything seen so far.

        The turn state machine takes one either side of a tool round to ask
        whether the round added anything. Ranges collapse into a set, so a
        round that re-read lines already seen reads as barren — which is the
        intent: re-reading is not progress, and the duplicate-call guard only
        catches the case where the ARGUMENTS were identical too.

        A dispatched subagent's findings count, which is why this reads both
        buckets: a round that spent itself on one explore call and came back
        with three new locations is the most productive round a turn can have.
        """
        return (
            frozenset(self.files),
            frozenset(
                (rel, start, end)
                for source in (self._ranges, self._elsewhere)
                for rel, ranges in source.items()
                for start, end in ranges
            ),
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

        if name in _REPORTED:
            return

        if name == "read_image":
            # An image has no line numbers. Recording the FILE means a claim
            # citing the bare path is verifiable, while any path:line the model
            # invents about an image still fails the gate — which is correct.
            #
            # It returns here rather than falling through on purpose: the rest
            # of this method mines the OUTPUT, and read_image's output is a
            # vision model's prose. A "loop.py:193" appearing inside a
            # description of a diagram was never displayed to anyone, so
            # treating it as evidence would license exactly the invented
            # citation this ledger exists to catch.
            if isinstance(path, str) and path:
                self.record_file(path)
            return

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
