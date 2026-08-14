"""The grounding gate: mechanical verification of file:line claims.

Runs between the LLM's final text and the output channels — nothing reaches
the speakers (or the screen) carrying a reference this gate could not verify.
Verification is file:line only: the file exists inside the repo and the line
number is within its line count (spec "Grounding gate", amended 2026-07-03).
Unverifiable references are stripped from the speakable text and replaced
with a single honest hedge sentence.

Amended 2026-08-02 (M10), with sign-off: a reference whose FILE verifies but
whose LINE is out of range is now rewritten to the bare path rather than
deleted outright, and the hedge narrows to "I couldn't confirm the exact
line." Nothing unverified survives either way — the path itself passed the
same existence and containment checks — but the answer keeps information the
user can act on, and stops claiming doubt about a file we just confirmed
exists. A reference to a file that does not exist, or that escapes the repo,
is still removed whole and still gets the broader hedge.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_references

HEDGE = "I couldn't verify that location."
# Used when every failing reference named a REAL file and only its line number
# was wrong. The path survives verification, so it survives into the answer;
# only the number is dropped, and the hedge narrows to match. Saying "I
# couldn't verify that location" of a file we just confirmed exists overstates
# the doubt, and deleting the path throws away information the user can use.
LINE_HEDGE = "I couldn't confirm the exact line."

# Distinct cited paths cached before the caches are dropped and rebuilt.
# Comfortably above any real repo's file count; it exists to bound a model
# that invents paths, not to bound normal use.
_CACHE_CEILING = 4096


@dataclass(frozen=True)
class GroundedText:
    speech_text: str
    citations: tuple[Citation, ...]
    unverified: tuple[str, ...]


def _literal(value: str) -> Callable[[re.Match[str]], str]:
    """A re.sub replacement that inserts `value` verbatim.

    A function, not a string: a Windows path in a string replacement would be
    read as regex escapes (\g, \1) and mangle the citation it is repairing.
    """
    return lambda _match: value


class GroundingGate:
    def __init__(self, root: Path):
        self.root = root
        # Resolved once. Path.resolve() is a real filesystem operation, and
        # this was being recomputed for every citation of every check.
        self._root = root.resolve()
        # rel -> resolved path inside the repo, or None if it escapes. For a
        # fixed root this mapping is deterministic, so it never needs
        # invalidating; it exists purely to avoid re-running resolve().
        self._targets: dict[str, Path | None] = {}
        # rel -> ((st_mtime_ns, st_size), line_count|None). The gate re-reads
        # every cited file on every check, and on the voice path a check runs
        # once per spoken sentence — the same two or three files, over and
        # over, for the whole answer.
        self._line_counts: dict[str, tuple[tuple[int, int], int | None]] = {}

    async def check(self, text: str) -> GroundedText:
        # Real-time discipline: every file read happens off the event loop.
        return await asyncio.to_thread(self._check_sync, text)

    def _check_sync(self, text: str) -> GroundedText:
        line_counts: dict[str, int | None] = {}
        verified: list[Citation] = []
        unverified: list[str] = []
        seen_ok: set[tuple[str, int]] = set()
        seen_bad: set[str] = set()

        # ref -> what replaces it in the speakable text. A reference whose FILE
        # is real keeps the path and loses only the line number; one whose file
        # does not exist (or escapes the repo) is removed entirely, because
        # nothing about it was verified.
        replacement: dict[str, str] = {}
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
                    replacement[ref] = rel if count is not None else ""

        speech = text
        if unverified:
            for ref in unverified:
                rel, _, line_str = ref.rpartition(":")
                # Match both the normalized (/) and original (\) spellings;
                # \b after the line number keeps app.py:5 from eating app.py:55.
                pattern = re.compile(
                    re.escape(rel).replace("/", r"[/\\]") + ":" + line_str + r"\b"
                )
                # A function replacement, not a string: a Windows path in the
                # replacement would otherwise be read as regex escapes.
                speech = pattern.sub(_literal(replacement[ref]), speech)
            speech = re.sub(r"[ \t]{2,}", " ", speech).strip()
            # Narrow the hedge only when every failure was a real file with a
            # bad line. One missing path anywhere means the broader hedge.
            hedge = LINE_HEDGE if all(replacement.values()) else HEDGE
            speech = f"{speech} {hedge}" if speech else hedge

        return GroundedText(
            speech_text=speech,
            citations=tuple(verified),
            unverified=tuple(unverified),
        )

    def _resolve_inside(self, rel: str) -> Path | None:
        """Resolve a repo-relative citation, or None if it escapes the repo.

        Memoised on `rel`: resolve() is a filesystem operation and a
        hallucinated path is cited just as often as a real one, so both
        outcomes are worth caching. The caches are cleared wholesale past a
        generous ceiling — a model that invents thousands of distinct paths
        must not grow this without bound.
        """
        if rel in self._targets:
            return self._targets[rel]
        if len(self._targets) > _CACHE_CEILING:
            self._targets.clear()
            self._line_counts.clear()
        resolved = (self._root / rel).resolve()
        target: Path | None = resolved
        try:
            resolved.relative_to(self._root)
        except ValueError:
            target = None
        self._targets[rel] = target
        return target

    def _count_lines(self, rel: str) -> int | None:
        """Line count of a repo file, or None if missing/unreadable/escaping.

        Cached on (st_mtime_ns, st_size). Both, not just mtime: filesystem
        timestamp granularity is coarse enough that a file edited twice within
        one tick would keep a stale count, and a same-tick truncation is
        exactly the case that would let an out-of-range line verify. Size
        closes that hole. A stat() is far cheaper than a full read, and it is
        what makes the cache safe to hold across turns.
        """
        target = self._resolve_inside(rel)
        if target is None:
            return None  # ../-style escape — never verify outside the repo
        try:
            stat = target.stat()
        except OSError:
            self._line_counts.pop(rel, None)
            return None
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._line_counts.get(rel)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        count: int | None = None
        if target.is_file():
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                count = None
            else:
                # splitlines(), deliberately not content.count("\n"): it also
                # splits on \r, \v, \f, \x1c-\x1e, \x85 and  / , and
                # changing that would change which line numbers verify.
                count = len(content.splitlines())
        self._line_counts[rel] = (stamp, count)
        return count
