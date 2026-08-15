"""Grounding eval: score the agent's citations against expected file:line.

This is the metric for VISION.md's open question "how do we measure
cited-the-right-file:line". Cases are YAML. Two forms, and a case may use
both:

    - question: "Where is greet defined?"
      expected:
        - {file: utils/helpers.py, line: 1}      # must cite EVERY one (+/-5)

    - question: "Where are tool calls dispatched?"
      expected_any:
        - {file: guards.py, line: 62}            # at least one of these
        - {file: guards.py, line: 104}

    - question: "How does the retry backoff work?"
      must_not_cite: "*"                          # must admit ignorance

`must_not_cite` closes a real gap: VISION.md's third success criterion is
that Pyrrhon admits ignorance rather than inventing, and nothing measured it.
`"*"` means "no citations at all"; a list of paths means "not these".

`expected` requires every entry (amended 2026-08-15, M13 — it used to pass on
the first match, so a case demanding three citations passed on one).
`expected_any` is the deliberate version of that looser rule, for questions
where several locations are equally correct answers.

The runner also collects a TurnTrace per case, so the same command doubles as
the latency harness — the numbers behind M10's stage-by-stage claims:

    uv run python -m pyrrhon.evals.grounding evals/grounding.yaml --repo .
    ... --repeat 5 --json baseline.json
    ... --repeat 5 --compare baseline.json     # non-zero exit on regression
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pyrrhon.core.events import Citation

LINE_TOLERANCE = 5

# Metrics carried into --json and checked by --compare. first_speech_ms is the
# one that matters most: it is what the user actually waits through.
LATENCY_KEYS = (
    "first_speech_ms",
    "ttft_ms",
    "total_ms",
    "llm_ms",
    "tool_wall_ms",
    "tool_total_ms",
    "gate_ms",
)

# How much slower than baseline a median may drift before --compare fails.
DEFAULT_TOLERANCE = 1.20


@dataclass
class EvalReport:
    total: int
    passed: int
    failures: list[str]
    # compare=False: existing tests assert `report == EvalReport(total=..., ...)`
    # and must keep doing so. Traces are diagnostics, not part of the result.
    traces: list[dict] = field(default_factory=list, compare=False)

    @property
    def latency(self) -> dict:
        return summarize(self.traces)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[int(index)]


def summarize(traces: list[dict]) -> dict:
    """Median and p95 per metric. Median, not mean: one cold start or one
    provider hiccup should not move the number the whole comparison rests on."""
    out: dict = {}
    for key in LATENCY_KEYS:
        values = [t[key] for t in traces if isinstance(t.get(key), (int, float))]
        if values:
            out[key] = {
                "median": _percentile(values, 50),
                "p95": _percentile(values, 95),
                "n": len(values),
            }
    return out


def run_eval(yaml_path: Path, agent_factory, repeat: int = 1) -> EvalReport:
    """Run every case with a fresh agent from `agent_factory()`.

    Sync on purpose: it owns its own event loop via asyncio.run, so the CLI
    (and any script) can call it directly.
    """
    cases = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or []
    return asyncio.run(_run_cases(cases, agent_factory, repeat))


async def _run_cases(cases: list[dict], agent_factory, repeat: int = 1) -> EvalReport:
    passed = 0
    failures: list[str] = []
    traces: list[dict] = []
    for _ in range(max(1, repeat)):
        for case in cases:
            agent = agent_factory()
            citations = [
                event
                async for event in agent.run_turn([], case["question"])
                if isinstance(event, Citation)
            ]
            trace = getattr(agent, "last_trace", None)
            if trace is not None:
                traces.append(trace.as_dict())
            problem = _check(citations, case)
            if problem is None:
                passed += 1
            else:
                failures.append(f"{case['question']!r}: {problem}")
    return EvalReport(
        total=len(cases) * max(1, repeat),
        passed=passed,
        failures=failures,
        traces=traces,
    )


def _check(citations: list[Citation], case: dict) -> str | None:
    """None when the case passes, else a one-line explanation."""
    got = ", ".join(f"{c.file}:{c.line}" for c in citations) or "no citations"

    forbidden = case.get("must_not_cite")
    if forbidden is not None:
        if forbidden == "*":
            if citations:
                return f"expected no citations (admit ignorance), got {got}"
        else:
            banned = {f if isinstance(f, str) else f["file"] for f in forbidden}
            hit = [c for c in citations if c.file in banned]
            if hit:
                return f"must not cite {sorted(banned)}, got {got}"

    expected = case.get("expected")
    if expected:
        missing = [e for e in expected if not _one_matches(citations, e)]
        if missing:
            want = ", ".join(f"{e['file']}:{e['line']}" for e in missing)
            return f"missing expected citation(s) {want}, got {got}"

    any_of = case.get("expected_any")
    if any_of and not any(_one_matches(citations, e) for e in any_of):
        want = " | ".join(f"{e['file']}:{e['line']}" for e in any_of)
        return f"expected one of {want}, got {got}"
    return None


def _one_matches(citations: list[Citation], exp: dict) -> bool:
    """Did any citation land on `exp`'s file within LINE_TOLERANCE lines?"""
    return any(
        citation.file == exp["file"]
        and citation.line is not None
        and abs(citation.line - exp["line"]) <= LINE_TOLERANCE
        for citation in citations
    )


def compare_latency(
    current: dict, baseline: dict, tolerance: float = DEFAULT_TOLERANCE
) -> list[str]:
    """Metrics whose median regressed past `tolerance`, as printable lines."""
    regressions: list[str] = []
    for key, now in current.items():
        was = baseline.get(key)
        if not was or not was.get("median"):
            continue
        ratio = now["median"] / was["median"]
        if ratio > tolerance:
            regressions.append(
                f"{key}: {was['median']:.0f}ms -> {now['median']:.0f}ms "
                f"({ratio:.2f}x, tolerance {tolerance:.2f}x)"
            )
    return regressions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pyrrhon.evals.grounding",
        description="Score the agent's file:line citations, and time the turns.",
    )
    parser.add_argument("yaml_path", type=Path, help="Eval case file (YAML)")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("tests/fixtures/sample_repo"),
        help="Repo the questions are about (default: the test fixture repo)",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Run the whole case set N times, for latency variance",
    )
    parser.add_argument("--json", type=Path, help="Write the full report here")
    parser.add_argument(
        "--compare", type=Path,
        help="Baseline JSON from a previous --json run; regressions exit non-zero",
    )
    parser.add_argument(
        "--tolerance", type=float, default=DEFAULT_TOLERANCE,
        help=f"Allowed median slowdown vs baseline (default {DEFAULT_TOLERANCE})",
    )
    args = parser.parse_args(argv)

    # Imported here, not at module top: only the CLI needs a real,
    # API-key-backed agent — unit tests inject FakeLLM-backed factories.
    from pyrrhon.repl import build_agent

    repo_root = args.repo.resolve()
    report = run_eval(args.yaml_path, lambda: build_agent(repo_root), args.repeat)
    print(f"grounding eval: {report.passed}/{report.total} passed")
    for failure in report.failures:
        print(f"  FAIL {failure}")

    latency = report.latency
    for key, stats in latency.items():
        print(f"  {key:<16} median {stats['median']:7.0f}ms   p95 {stats['p95']:7.0f}ms")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "total": report.total,
                    "passed": report.passed,
                    "failures": report.failures,
                    "latency": latency,
                    "traces": report.traces,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  wrote {args.json}")

    regressed: list[str] = []
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        regressed = compare_latency(
            latency, baseline.get("latency", {}), args.tolerance
        )
        for line in regressed:
            print(f"  REGRESSION {line}")

    return 0 if report.passed == report.total and not regressed else 1


if __name__ == "__main__":
    raise SystemExit(main())
