"""Per-turn latency telemetry: where the wall clock actually goes.

Voice-first means latency *is* the product, so every optimization has to be
provable. `Session.last_turn_latency_ms` already measures the number that
matters most — user_text to first SpeechChunk — but it is a single scalar, so
it can tell you a turn got slower and nothing about which part.

This breaks that number into the pieces you can act on: the pre-flight work
before round one, each LLM round (time-to-first-token separately from total,
because streaming decouples them), the tools each round ran, the grounding
gate, and the two tail calls (self-correction retry, forced answer).

Everything is `time.perf_counter()`. Not `time.time()`, which can step
backwards across an NTP correction and produce negative durations — and
deliberately not `time.monotonic()` either, despite that being the obvious
choice: on Windows `monotonic` is a ~15.6 ms-granular tick counter, so every
span shorter than one tick measures exactly 0.0. `perf_counter` is monotonic
too (`time.get_clock_info("perf_counter").monotonic is True`) and resolves to
~100 ns, which is the difference between a usable gate/tool breakdown and a
column of zeroes.

Two properties are deliberately recorded side by side on every round:

    tool_wall_ms   wall clock for the whole tool phase
    tool_total_ms  sum of the individual tool durations

Sequential dispatch makes those equal. Concurrent dispatch makes wall
approach max() while total stays at sum(). The ratio between them is the
direct measurement of that change — the trace proves the optimization rather
than asserting it.

Nothing here does I/O or logging on the hot path; a span is two clock reads
and a float add, so it is safe to leave enabled in production. Spans record
in a `finally`, so a turn cancelled mid-flight by barge-in still yields a
usable partial trace instead of an empty one.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


def _ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


@contextmanager
def _span(record) -> Iterator[None]:
    """Time the block and hand the elapsed milliseconds to `record`.

    Records in a `finally`, so an exception or a CancelledError (barge-in
    cancels the turn task mid-`await`) still books the time spent.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        record(_ms_since(start))


@dataclass
class ToolSpan:
    """One tool invocation. Kept as a list rather than folded into a dict so a
    round that calls the same tool twice stays legible."""

    name: str
    ms: float


@dataclass
class RoundTrace:
    """One LLM round: the model call, the tools it asked for, the gate work."""

    index: int
    started: float = field(default_factory=time.perf_counter)
    llm_ttft_ms: float | None = None  # None on the non-streaming path
    llm_total_ms: float = 0.0
    tools: list[ToolSpan] = field(default_factory=list)
    tool_wall_ms: float = 0.0
    gate_ms: float = 0.0

    @property
    def tool_total_ms(self) -> float:
        """Sum of the individual tool durations — `sum()`, not `max()`."""
        return sum(span.ms for span in self.tools)

    @property
    def tool_ms(self) -> dict[str, float]:
        """Per-tool-name totals, for the human-readable summary."""
        totals: dict[str, float] = {}
        for span in self.tools:
            totals[span.name] = totals.get(span.name, 0.0) + span.ms
        return totals

    @property
    def parallel_speedup(self) -> float:
        """tool_total / tool_wall — 1.0 when sequential, ~N when N ran at once.

        Returns 1.0 when the round ran no tools, so callers can average it.
        """
        if self.tool_wall_ms <= 0.0 or not self.tools:
            return 1.0
        return self.tool_total_ms / self.tool_wall_ms

    @contextmanager
    def time_llm(self) -> Iterator[None]:
        with _span(lambda ms: setattr(self, "llm_total_ms", ms)):
            yield

    def mark_ttft(self) -> None:
        """Stamp time-to-first-token, once per round (later chunks are noise).

        Measured from `started`, which is stamped when the round is created —
        immediately before the model call — so the caller never has to thread
        a start time through the streaming loop.
        """
        if self.llm_ttft_ms is None:
            self.llm_ttft_ms = _ms_since(self.started)

    @contextmanager
    def time_tool(self, name: str) -> Iterator[None]:
        with _span(lambda ms: self.tools.append(ToolSpan(name=name, ms=ms))):
            yield

    @contextmanager
    def time_tool_round(self) -> Iterator[None]:
        with _span(lambda ms: setattr(self, "tool_wall_ms", ms)):
            yield

    @contextmanager
    def time_gate(self) -> Iterator[None]:
        with _span(lambda ms: setattr(self, "gate_ms", self.gate_ms + ms)):
            yield


@dataclass
class TurnTrace:
    """The whole turn. Created by `Agent.run_turn`, read by `Session`."""

    started: float = field(default_factory=time.perf_counter)
    preamble_ms: float = 0.0
    rounds: list[RoundTrace] = field(default_factory=list)
    retry_ms: float = 0.0
    forced_answer_ms: float = 0.0
    first_speech_ms: float | None = None
    total_ms: float | None = None
    prompt_chars: int = 0
    schema_chars: int = 0
    turn_type: str = "repo_question"
    streamed: bool = False

    # -- recording -----------------------------------------------------------

    @contextmanager
    def time_preamble(self) -> Iterator[None]:
        with _span(lambda ms: setattr(self, "preamble_ms", ms)):
            yield

    @contextmanager
    def time_retry(self) -> Iterator[None]:
        with _span(lambda ms: setattr(self, "retry_ms", ms)):
            yield

    @contextmanager
    def time_forced_answer(self) -> Iterator[None]:
        with _span(lambda ms: setattr(self, "forced_answer_ms", ms)):
            yield

    def begin_round(self) -> RoundTrace:
        round_trace = RoundTrace(index=len(self.rounds))
        self.rounds.append(round_trace)
        return round_trace

    def mark_first_speech(self) -> None:
        """Stamp the money metric, once. Idempotent: later chunks don't move it."""
        if self.first_speech_ms is None:
            self.first_speech_ms = _ms_since(self.started)

    def finish(self) -> None:
        self.total_ms = _ms_since(self.started)

    # -- reading -------------------------------------------------------------

    @property
    def llm_ms(self) -> float:
        return sum(r.llm_total_ms for r in self.rounds)

    @property
    def tool_wall_ms(self) -> float:
        return sum(r.tool_wall_ms for r in self.rounds)

    @property
    def tool_total_ms(self) -> float:
        return sum(r.tool_total_ms for r in self.rounds)

    @property
    def gate_ms(self) -> float:
        return sum(r.gate_ms for r in self.rounds)

    @property
    def tool_calls(self) -> int:
        return sum(len(r.tools) for r in self.rounds)

    @property
    def ttft_ms(self) -> float | None:
        """Time-to-first-token of the FIRST round — the streaming equivalent of
        first_speech_ms, and unaffected by how long the answer turned out."""
        return self.rounds[0].llm_ttft_ms if self.rounds else None

    def as_dict(self) -> dict:
        """Flat, JSON-safe view for the latency harness and `--json` output."""
        return {
            "total_ms": self.total_ms,
            "first_speech_ms": self.first_speech_ms,
            "ttft_ms": self.ttft_ms,
            "preamble_ms": self.preamble_ms,
            "llm_ms": self.llm_ms,
            "tool_wall_ms": self.tool_wall_ms,
            "tool_total_ms": self.tool_total_ms,
            "gate_ms": self.gate_ms,
            "retry_ms": self.retry_ms,
            "forced_answer_ms": self.forced_answer_ms,
            "rounds": len(self.rounds),
            "tool_calls": self.tool_calls,
            "prompt_chars": self.prompt_chars,
            "schema_chars": self.schema_chars,
            "turn_type": self.turn_type,
            "streamed": self.streamed,
            "per_round": [
                {
                    "index": r.index,
                    "llm_ttft_ms": r.llm_ttft_ms,
                    "llm_total_ms": r.llm_total_ms,
                    "tool_wall_ms": r.tool_wall_ms,
                    "tool_total_ms": r.tool_total_ms,
                    "parallel_speedup": r.parallel_speedup,
                    "gate_ms": r.gate_ms,
                    "tool_ms": r.tool_ms,
                }
                for r in self.rounds
            ],
        }

    def summary(self) -> str:
        """One-line human-readable breakdown, for /debug and the status bar."""

        def fmt(value: float | None) -> str:
            return "—" if value is None else f"{value:.0f}ms"

        parts = [
            f"turn {fmt(self.total_ms)}",
            f"first-speech {fmt(self.first_speech_ms)}",
            f"ttft {fmt(self.ttft_ms)}",
            f"llm {fmt(self.llm_ms)}",
            f"tools {fmt(self.tool_wall_ms)}",
            f"gate {fmt(self.gate_ms)}",
        ]
        if self.retry_ms:
            parts.append(f"retry {fmt(self.retry_ms)}")
        if self.forced_answer_ms:
            parts.append(f"forced {fmt(self.forced_answer_ms)}")
        if self.tool_calls:
            parts.append(
                f"{self.tool_calls} tool call(s) over {len(self.rounds)} round(s)"
            )
        return " | ".join(parts)
