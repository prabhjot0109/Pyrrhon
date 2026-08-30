"""How far one turn is allowed to go, and how to tell when it has gone far enough.

`_run_turn` used to carry the turn's state as five loose locals — a `range()`
counter, a `ToolGuard`, two booleans and a per-round resume count — and
reconstruct the reason it stopped from whichever branch happened to break out.
That reason is a domain concept: the forced-answer path wants it, the trace
wants it, and both were inferring it from context.

So it gets a type. `TurnState` is what this turn has spent, `TurnPolicy` is
what it is allowed to spend, and `decide()` is the one function that compares
them. Nothing here does I/O, touches history, or knows what an LLM is.

The policy is a TABLE keyed by (turn type, voice active), not a chain of
conditionals, because "which turns get no tools" is exactly one fact and it
had already started living in two places — `turn_type.needs_tools` and the
`schemas = ... if needs_tools(...)` line in the loop. `needs_tools` is derived
from the table now.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

StopReason = Literal["rounds", "budget", "diminishing", "answered"]

# Consecutive rounds that added nothing before the turn is called off. Counts
# BARREN rounds, not tool rounds — the reference counts forced continuations,
# and a literal three-tool-round threshold would cut off an investigation that
# is working. Three rounds in a row that opened no new line range and named no
# new path is not an investigation; it is a loop, and the EvidenceLedger is the
# only thing in the harness that can tell those apart. A token count cannot: a
# round can be expensive and productive, or cheap and decisive.
MAX_BARREN_ROUNDS = 3

# Fired as the budget is APPROACHED, and pointed the opposite way from Claude
# Code's equivalent. Theirs says "keep working, do not summarize", because
# their failure mode is a model that stops early. Pyrrhon's failure mode is the
# reverse — a spoken turn that spends four more rounds has already lost — so
# this one says land it. Do not copy the reference's wording back in.
LAND_NUDGE = (
    "You are close to this turn's tool budget. Answer now with what you have, "
    "citing only path:line locations you actually saw. Make at most one more "
    "tool call, and only if the answer is impossible without it."
)


@dataclass(frozen=True)
class TurnPolicy:
    """What one turn is allowed to spend.

    `withheld` names the tools this turn does NOT get. `None` means it gets no
    tools at all; an empty frozenset means the full belt. Read it through
    `belt_for`, never directly, so the polarity is decided in one place.
    """

    max_rounds: int
    max_tool_chars: int
    withheld: frozenset[str] | None
    nudge_at: float

    def belt_for(self, names: Iterable[str]) -> list[str] | None:
        """The tool names this turn may use, or None when it gets no belt."""
        if self.withheld is None:
            return None
        return [name for name in names if name not in self.withheld]


@dataclass
class TurnState:
    """What this turn has spent. One per turn, mutable, owned by `_run_turn`."""

    rounds: int = 0
    tool_chars: int = 0
    # Context-window overflows recovered from. Accumulates per TURN.
    recoveries: int = 0
    nudges_issued: set[str] = field(default_factory=set)
    barren_rounds: int = 0
    # Resumes of a reply cut off at max_tokens. Per ROUND, not per turn: it is
    # reset by `note_round`, so a long investigation is not denied a resume
    # because an earlier round used one. Folding it into `recoveries` would
    # silently let a long turn resume once and then never again.
    truncation_resumes: int = 0

    def note_round(self, *, productive: bool) -> None:
        """Book one completed tool round.

        `productive` is whether the round's tool results added anything to the
        evidence ledger — a new path, or a line range not already seen.
        """
        self.rounds += 1
        self.truncation_resumes = 0
        self.barren_rounds = 0 if productive else self.barren_rounds + 1

    def issue_nudge(self, key: str) -> bool:
        """True the first time `key` is issued this turn, False after.

        The once-only rule lives with the data because it has two writers:
        `decide` issues the land nudge, and the loop issues the invalid-tool
        one. A boolean per nudge beside them is how the old code drifted.
        """
        if key in self.nudges_issued:
            return False
        self.nudges_issued.add(key)
        return True


@dataclass(frozen=True)
class Continue:
    """Run another round. `nudge` is a user message to inject first, if any."""

    nudge: str | None = None


@dataclass(frozen=True)
class Stop:
    reason: StopReason


def decide(state: TurnState, policy: TurnPolicy) -> Continue | Stop:
    """Should this turn run another round, and does the model need telling?

    Not pure: issuing the land nudge marks it issued, because that invariant
    belongs with the state rather than with each caller. Nothing else here
    mutates, and there is no I/O.

    The hard caps are checked before the soft one so the trace names the limit
    that actually fired: at the end of a runaway turn the round cap and the
    barren count are usually both true, and "rounds" is the honest answer.
    """
    if state.rounds >= policy.max_rounds:
        return Stop(reason="rounds")
    if state.tool_chars >= policy.max_tool_chars:
        return Stop(reason="budget")
    if state.barren_rounds >= MAX_BARREN_ROUNDS:
        return Stop(reason="diminishing")
    if _approaching(state, policy) and state.issue_nudge("land"):
        return Continue(nudge=LAND_NUDGE)
    return Continue()


def _approaching(state: TurnState, policy: TurnPolicy) -> bool:
    return (
        state.rounds >= policy.max_rounds * policy.nudge_at
        or state.tool_chars >= policy.max_tool_chars * policy.nudge_at
    )
