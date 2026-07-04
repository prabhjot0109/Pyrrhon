"""Grounding eval v0: score the agent's citations against expected file:line.

This is the metric for VISION.md's open question "how do we measure
cited-the-right-file:line". Cases are YAML: a list of
{question, expected: [{file, line}]}. A case passes if any Citation the
agent emits matches an expected entry — file equal exactly, line within ±5.

Run against the checked-in case set (real LLM, needs an API key):

    uv run python -m pyrrhon.evals.grounding evals/grounding.yaml
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import yaml

from pyrrhon.core.events import Citation

LINE_TOLERANCE = 5


@dataclass
class EvalReport:
    total: int
    passed: int
    failures: list[str]


def run_eval(yaml_path: Path, agent_factory) -> EvalReport:
    """Run every case with a fresh agent from `agent_factory()`.

    Sync on purpose: it owns its own event loop via asyncio.run, so the CLI
    (and any script) can call it directly.
    """
    cases = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or []
    return asyncio.run(_run_cases(cases, agent_factory))


async def _run_cases(cases: list[dict], agent_factory) -> EvalReport:
    passed = 0
    failures: list[str] = []
    for case in cases:
        agent = agent_factory()
        citations = [
            event
            async for event in agent.run_turn([], case["question"])
            if isinstance(event, Citation)
        ]
        if _matches(citations, case["expected"]):
            passed += 1
        else:
            got = ", ".join(f"{c.file}:{c.line}" for c in citations) or "no citations"
            want = ", ".join(f"{e['file']}:{e['line']}" for e in case["expected"])
            failures.append(f"{case['question']!r}: expected {want}, got {got}")
    return EvalReport(total=len(cases), passed=passed, failures=failures)


def _matches(citations: list[Citation], expected: list[dict]) -> bool:
    for exp in expected:
        for citation in citations:
            if (
                citation.file == exp["file"]
                and citation.line is not None
                and abs(citation.line - exp["line"]) <= LINE_TOLERANCE
            ):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pyrrhon.evals.grounding",
        description="Score the agent's file:line citations against expected answers.",
    )
    parser.add_argument("yaml_path", type=Path, help="Eval case file (YAML)")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("tests/fixtures/sample_repo"),
        help="Repo the questions are about (default: the test fixture repo)",
    )
    args = parser.parse_args(argv)

    # Imported here, not at module top: only the CLI needs a real,
    # API-key-backed agent — unit tests inject FakeLLM-backed factories.
    from pyrrhon.repl import build_agent

    repo_root = args.repo.resolve()
    report = run_eval(args.yaml_path, lambda: build_agent(repo_root))
    print(f"grounding eval: {report.passed}/{report.total} passed")
    for failure in report.failures:
        print(f"  FAIL {failure}")
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
