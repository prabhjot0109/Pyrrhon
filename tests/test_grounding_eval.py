from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.grounding.gate import GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.evals.grounding import EvalReport, run_eval
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

TWO_CASES = """\
- question: "Where is the greet function defined?"
  expected:
    - {file: utils/helpers.py, line: 1}
- question: "Where is greet called from?"
  expected:
    - {file: app.py, line: 5}
"""

ONE_CASE = """\
- question: "Where is greet?"
  expected:
    - {file: utils/helpers.py, line: 1}
"""


def make_factory(scripts: list[list[LLMReply]]):
    """One scripted reply-list per eval case, consumed in order."""
    queue = [list(replies) for replies in scripts]

    def factory() -> Agent:
        return Agent(
            llm=FakeLLM(queue.pop(0)),
            tools=[],
            system_prompt="You are a test agent.",
            repo_root=FIXTURE,
            grounding_gate=GroundingGate(FIXTURE),
            allow_retry=False,
        )

    return factory


# NOTE: run_eval calls asyncio.run() internally, so these tests are sync defs
# (an async test would already be inside a running loop and asyncio.run fails).


def test_all_cases_pass(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(TWO_CASES, encoding="utf-8")
    factory = make_factory(
        [
            [LLMReply(text="greet is defined at utils/helpers.py:1.")],
            [LLMReply(text="It is called from app.py:5.")],
        ]
    )
    report = run_eval(yaml_path, factory)
    assert report == EvalReport(total=2, passed=2, failures=[])


def test_line_within_tolerance_of_five_passes(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(
        '- question: "Where is main?"\n  expected:\n    - {file: app.py, line: 4}\n',
        encoding="utf-8",
    )
    # app.py:8 is a real line; |8 - 4| = 4 <= 5 → pass.
    factory = make_factory([[LLMReply(text="main runs from app.py:8.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 1
    assert report.failures == []


def test_failure_lists_question_expected_and_got(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    factory = make_factory([[LLMReply(text="I could not find it.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 0
    assert len(report.failures) == 1
    assert "Where is greet?" in report.failures[0]
    assert "utils/helpers.py:1" in report.failures[0]
    assert "no citations" in report.failures[0]


def test_wrong_file_fails_even_if_line_is_close(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    # app.py:1 is a real, verifiable citation — but the file must match exactly.
    factory = make_factory([[LLMReply(text="greet is at app.py:1.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 0
    assert "app.py:1" in report.failures[0]


# -- must_not_cite: VISION.md criterion 3, "admits ignorance" ---------------
#
# Nothing measured this before. The other cases all check that a citation is
# RIGHT; this checks that a citation is absent when it should be.


def test_must_not_cite_star_passes_when_the_agent_admits_ignorance(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(
        '- question: "How does the retry backoff work?"\n  must_not_cite: "*"\n',
        encoding="utf-8",
    )
    factory = make_factory([[LLMReply(text="I'm not certain — I don't see one.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 1


def test_must_not_cite_star_fails_when_the_agent_cites_anything(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(
        '- question: "How does the retry backoff work?"\n  must_not_cite: "*"\n',
        encoding="utf-8",
    )
    factory = make_factory([[LLMReply(text="It backs off in utils/helpers.py:1.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 0
    assert "admit ignorance" in report.failures[0]


def test_must_not_cite_can_name_specific_files(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(
        '- question: "Where is greet?"\n'
        "  expected:\n    - {file: utils/helpers.py, line: 1}\n"
        "  must_not_cite: [app.py]\n",
        encoding="utf-8",
    )
    factory = make_factory([[LLMReply(text="greet is at utils/helpers.py:1, used by app.py:5.")]])
    report = run_eval(yaml_path, factory)
    assert report.passed == 0
    assert "must not cite" in report.failures[0]


# -- the latency harness (consumes Stage 0's traces) ------------------------


def test_traces_are_collected_without_changing_equality(tmp_path: Path):
    """EvalReport.traces is compare=False, so the existing exact-equality
    assertions above keep working."""
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    factory = make_factory([[LLMReply(text="greet is at utils/helpers.py:1.")]])
    report = run_eval(yaml_path, factory)

    assert report == EvalReport(total=1, passed=1, failures=[])  # traces ignored
    assert len(report.traces) == 1
    assert report.latency["total_ms"]["n"] == 1


def test_repeat_runs_the_set_n_times(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    factory = make_factory([[LLMReply(text="greet is at utils/helpers.py:1.")]] * 3)
    report = run_eval(yaml_path, factory, repeat=3)
    assert report.total == 3
    assert report.passed == 3
    assert len(report.traces) == 3


def test_compare_latency_flags_only_real_regressions():
    from pyrrhon.evals.grounding import compare_latency

    baseline = {"first_speech_ms": {"median": 100.0}, "gate_ms": {"median": 10.0}}
    current = {
        "first_speech_ms": {"median": 110.0},  # 1.10x — within tolerance
        "gate_ms": {"median": 30.0},           # 3.00x — a real regression
    }
    lines = compare_latency(current, baseline, tolerance=1.20)
    assert len(lines) == 1
    assert "gate_ms" in lines[0]


def test_compare_latency_ignores_metrics_absent_from_the_baseline():
    from pyrrhon.evals.grounding import compare_latency

    assert compare_latency({"ttft_ms": {"median": 900.0}}, {}, tolerance=1.2) == []
