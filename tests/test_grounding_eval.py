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
