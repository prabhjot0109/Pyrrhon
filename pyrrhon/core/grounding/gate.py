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

Amended 2026-08-15 (M13): with `require_provenance` on and an EvidenceLedger
supplied, verification becomes three-way. "This line exists" is not the same
claim as "we looked at this line", and repo_map hands the model a menu of real
paths and real line numbers — so a fabricated in-range citation used to pass
every check here. A reference whose file and line both verify but which no
tool result this turn actually displayed is now downgraded to the bare path,
like an out-of-range line, with its own narrower hedge. Off by default; the
unverified path is untouched either way.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_references
from pyrrhon.core.grounding.evidence import EvidenceLedger

HEDGE = "I couldn't verify that location."
# Used when every failing reference named a REAL file and only its line number
# was wrong. The path survives verification, so it survives into the answer;
# only the number is dropped, and the hedge narrows to match. Saying "I
# couldn't verify that location" of a file we just confirmed exists overstates
# the doubt, and deleting the path throws away information the user can use.
LINE_HEDGE = "I couldn't confirm the exact line."
# Used when a reference names a real file at a real line that no tool result
# this turn actually displayed. Distinct from LINE_HEDGE (the line is out of
# range) and from HEDGE (the file does not exist): here everything checks out
# except the one thing that matters, which is whether we looked.
LINE_UNSEEN_HEDGE = "I haven't actually opened that line this session."

@dataclass
class GateCounters:
    """What the gate did, since the gate was built. Diagnostics, never state.

    M16e is measured by one number and the gate is the only place that can
    produce it: the three-way sort in `_check_sync` (promote / hedge / strip)
    already exists, and until now the tally was thrown away — `last_unseen`
    kept one of the three arms and nothing kept the other two. A record rather
    than three ints on the Agent, because the arms are one taxonomy: a change
    that moves `stripped` down and `hedged` up has not improved anything, and
    only reading them together says so.

    `checks` counts calls, and a call is one streamed chunk on the streaming
    path and one whole answer off it — the spec's "sentence". A turn that runs
    the self-correction retry counts its answer twice, which inflates both
    halves of the rate slightly; the retry only fires off the streaming path,
    which is not the path the eval or voice takes.

    Mutated from inside `asyncio.to_thread`, which is safe only because gate
    checks are awaited one at a time by a single turn. Nothing here is worth a
    lock; if a second caller ever shares a gate, this is what to revisit.
    """

    checks: int = 0
    intervened: int = 0
    promoted: int = 0
    hedged: int = 0
    stripped: int = 0

    def __add__(self, other: GateCounters) -> GateCounters:
        """Totals across gates. The eval builds one agent per case, so the
        run-level number only exists if the per-case ones can be summed."""
        return GateCounters(
            checks=self.checks + other.checks,
            intervened=self.intervened + other.intervened,
            promoted=self.promoted + other.promoted,
            hedged=self.hedged + other.hedged,
            stripped=self.stripped + other.stripped,
        )

    @property
    def intervention_rate(self) -> float:
        """Fraction of checks in which the gate hedged or stripped something.

        The number M16e must move down. Zero checks reads as 0.0 rather than
        raising: an eval run that produced no prose intervened in nothing.
        """
        return self.intervened / self.checks if self.checks else 0.0


# Distinct cited paths cached before the caches are dropped and rebuilt.
# Comfortably above any real repo's file count; it exists to bound a model
# that invents paths, not to bound normal use.
_CACHE_CEILING = 4096


@dataclass(frozen=True)
class GroundedText:
    speech_text: str
    citations: tuple[Citation, ...]
    unverified: tuple[str, ...]
    # References to a real file at a real line that no tool result showed us.
    # Downgraded to the bare path rather than stripped: the path IS verified.
    # Defaulted so every existing construction site keeps compiling.
    unseen: tuple[str, ...] = ()


def _literal(value: str) -> Callable[[re.Match[str]], str]:
    r"""A re.sub replacement that inserts `value` verbatim.

    A function, not a string: a Windows path in a string replacement would be
    read as regex escapes (\g, \1) and mangle the citation it is repairing.

    Raw docstring for the same reason it is describing: unescaped, `\g` is an
    invalid escape sequence, which is a SyntaxWarning today and a SyntaxError
    in a later Python.
    """
    return lambda _match: value


def _hedge_for(unverified: list[str], replacement: dict[str, str]) -> str:
    """One hedge, matched to the WEAKEST claim among the failures present.

    Ordered by severity: an invented path is a worse claim than a bad line
    number, which is worse than a real line we simply did not open. Saying the
    mildest of the three while a fabricated path is also in the sentence would
    understate what went wrong. Callers reach here only when something failed,
    so the fall-through case is "everything that failed was merely unopened".
    """
    if any(not replacement[ref] for ref in unverified):
        return HEDGE
    if unverified:
        return LINE_HEDGE
    return LINE_UNSEEN_HEDGE


class GroundingGate:
    def __init__(self, root: Path, require_provenance: bool = False):
        self.root = root
        # Off by default until the M13 eval says the pass rate holds. It is a
        # real tightening, and a false downgrade makes Pyrrhon sound unsure
        # about work it genuinely did — which is its own kind of dishonesty.
        self.require_provenance = require_provenance
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
        # Read by the grounding eval off the agent it built. Cumulative over
        # the gate's life, which is one agent, which is one eval case.
        self.counters = GateCounters()

    async def check(
        self, text: str, evidence: EvidenceLedger | None = None
    ) -> GroundedText:
        # Real-time discipline: every file read happens off the event loop.
        return await asyncio.to_thread(self._check_sync, text, evidence)

    def _check_sync(
        self, text: str, evidence: EvidenceLedger | None = None
    ) -> GroundedText:
        line_counts: dict[str, int | None] = {}
        verified: list[Citation] = []
        unverified: list[str] = []
        unseen: list[str] = []
        seen_ok: set[tuple[str, int]] = set()
        seen_bad: set[str] = set()
        # None means "do not enforce provenance", which happens two ways, and
        # collapsing them would be a bug: the flag is the rollout control,
        # while a missing ledger identifies a caller that predates this feature
        # (tests, the M0 path) and must keep working unchanged rather than
        # start hedging silently.
        ledger = evidence if self.require_provenance else None

        # ref -> what replaces it in the speakable text. A reference whose FILE
        # is real keeps the path and loses only the line number; one whose file
        # does not exist (or escapes the repo) is removed entirely, because
        # nothing about it was verified.
        replacement: dict[str, str] = {}
        for rel, line in extract_references(text):
            if rel not in line_counts:
                line_counts[rel] = self._count_lines(rel)
            count = line_counts[rel]
            in_range = count is not None and 1 <= line <= count
            ref = f"{rel}:{line}"
            if in_range and (ledger is None or ledger.observed(rel, line)):
                if (rel, line) not in seen_ok:
                    seen_ok.add((rel, line))
                    verified.append(Citation(file=rel, line=line))
            elif in_range:
                # Real file, real line, never shown to us this turn.
                if ref not in seen_bad:
                    seen_bad.add(ref)
                    unseen.append(ref)
                    replacement[ref] = rel
            else:
                if ref not in seen_bad:
                    seen_bad.add(ref)
                    unverified.append(ref)
                    replacement[ref] = rel if count is not None else ""

        speech = text
        # Verified refs are removed from SPEECH but kept in `citations`: the
        # screen shows path:line, the voice never says it. Spoken coordinates
        # are unusable — a listener cannot act on "app dot py colon twelve".
        rewrites = {**replacement, **{f"{c.file}:{c.line}": "" for c in verified}}
        if rewrites:
            for ref, target in rewrites.items():
                rel, _, line_str = ref.rpartition(":")
                # Match both the normalized (/) and original (\) spellings;
                # \b after the line number keeps app.py:5 from eating app.py:55.
                pattern = re.compile(
                    re.escape(rel).replace("/", r"[/\\]") + ":" + line_str + r"\b"
                )
                # A function replacement, not a string: a Windows path in the
                # replacement would otherwise be read as regex escapes.
                speech = pattern.sub(_literal(target), speech)
            speech = re.sub(r"[ \t]{2,}", " ", speech).strip()
        # The hedge is for UNVERIFIED claims only. A verified citation that was
        # merely moved to the screen must never make the agent sound unsure.
        if unverified or unseen:
            hedge = _hedge_for(unverified, replacement)
            speech = f"{speech} {hedge}" if speech else hedge

        # Counted from the sorted lists rather than incremented at each branch
        # above: one place to read, and the arms cannot drift apart. `hedged`
        # and `stripped` split `unverified` by what survived into the text —
        # a real file keeps its path, an invented one keeps nothing — and the
        # unseen arm is a hedge by construction.
        stripped = sum(1 for ref in unverified if not replacement[ref])
        self.counters.checks += 1
        self.counters.promoted += len(verified)
        self.counters.stripped += stripped
        self.counters.hedged += len(unverified) - stripped + len(unseen)
        if unverified or unseen:
            self.counters.intervened += 1

        return GroundedText(
            speech_text=speech,
            citations=tuple(verified),
            unverified=tuple(unverified),
            unseen=tuple(unseen),
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
