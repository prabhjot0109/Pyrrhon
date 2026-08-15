"""Act 2 eval: does Pyrrhon interrogate a questionable choice before writing?

VISION.md makes this a v1 success criterion — design mode "pushes back on at
least one questionable choice before writing a spec" — and nothing measured it,
so a prompt edit could quietly turn the skeptic into a yes-man. Act 1 has had a
grounding eval since M1; this is the missing half of the pair.

The check is mechanical on purpose, the same reasoning as the grounding gate:
an LLM judge would be slower, non-deterministic, and would itself need
evaluating before its verdicts meant anything. "Emitted an AskUser and did not
call write_spec on the opening turn" is a crude proxy for Socratic behaviour,
but it is a proxy that cannot drift — and a drifting metric is worse than a
blunt one, because it fails silently.

    uv run python -m pyrrhon.evals.design evals/design.yaml --repo .
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pyrrhon.core.events import AskUser, ToolCallStarted

SPEC_TOOL = "write_spec"


@dataclass
class DesignReport:
    total: int
    passed: int
    failures: list[str] = field(default_factory=list)


def _check_design(events: list, case: dict) -> str | None:
    """None when the case passes, else a one-line explanation."""
    asked = [e for e in events if isinstance(e, AskUser)]
    tools = [e.name for e in events if isinstance(e, ToolCallStarted)]

    if case.get("must_challenge") and not asked:
        return "expected a challenge (an AskUser question), got none"
    if case.get("must_not_write_spec") and SPEC_TOOL in tools:
        return f"called {SPEC_TOOL} before the reasoning was established"
    required = case.get("must_write")
    if required and SPEC_TOOL not in tools:
        return f"expected {SPEC_TOOL} for {required}, got tools: {tools or 'none'}"
    return None


async def _run_cases(cases: list[dict], session_factory, repeat: int) -> DesignReport:
    passed = 0
    failures: list[str] = []
    for _ in range(max(1, repeat)):
        for case in cases:
            session = session_factory()
            # Act 2's push-back is mode-gated: scoring an understand-mode turn
            # would measure the wrong prompt entirely.
            session.set_mode("design")
            events = [event async for event in session.run_turn(case["premise"])]
            problem = _check_design(events, case)
            if problem is None:
                passed += 1
            else:
                failures.append(f"{case['premise']!r}: {problem}")
    return DesignReport(
        # Not `passed == total` by construction: an empty case file must score
        # 0/0 rather than reporting a vacuous success.
        total=len(cases) * max(1, repeat),
        passed=passed,
        failures=failures,
    )


def run_design_eval(
    yaml_path: Path, session_factory: Callable[[], object], repeat: int = 1
) -> DesignReport:
    """Run every case with a fresh session from `session_factory()`.

    Sync on purpose, like run_eval: it owns its own event loop via asyncio.run,
    so the CLI (and any script) can call it directly.
    """
    cases = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or []
    return asyncio.run(_run_cases(cases, session_factory, repeat))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pyrrhon.evals.design",
        description="Score Act 2: does Pyrrhon push back before it writes a spec?",
    )
    parser.add_argument("yaml_path", type=Path, help="Eval case file (YAML)")
    parser.add_argument(
        "--repo", type=Path, default=Path("tests/fixtures/sample_repo"),
        help="Repo the design conversation happens in",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Run the whole case set N times (push-back is not deterministic)",
    )
    args = parser.parse_args(argv)

    # Imported here, not at module top: only the CLI needs a real,
    # API-key-backed session — unit tests inject scripted doubles.
    from pyrrhon.bootstrap import build_agent
    from pyrrhon.config.credentials import load_credentials
    from pyrrhon.core.session import Session

    # Same gap as the grounding eval: keys written by `pyrrhon --setup` were
    # invisible here. setdefault semantics mean a real env var still wins.
    load_credentials()

    repo_root = args.repo.resolve()
    report = run_design_eval(
        args.yaml_path, lambda: Session(build_agent(repo_root)), args.repeat
    )
    print(f"design eval: {report.passed}/{report.total} passed")
    for failure in report.failures:
        print(f"  FAIL {failure}")
    return 0 if report.total and report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
