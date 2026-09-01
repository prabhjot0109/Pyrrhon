from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.gate import GateCounters, GroundingGate
from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.evals.grounding import EvalReport, _check, run_eval
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


# -- `expected` means EVERY citation, not "at least one" (M13) --------------
#
# _matches returned True on the first expected entry that matched, so a case
# listing three required citations passed when the model produced one. Every
# case in evals/grounding.yaml lists exactly one, which is why nobody noticed —
# and which is why fixing it now costs nothing.


def test_expected_requires_every_listed_citation():
    case = {
        "question": "q",
        "expected": [
            {"file": "app.py", "line": 5},
            {"file": "utils/helpers.py", "line": 1},
        ],
    }
    partial = [Citation(file="app.py", line=5)]
    assert _check(partial, case) is not None  # one of two is a FAIL

    complete = [Citation(file="app.py", line=5), Citation(file="utils/helpers.py", line=1)]
    assert _check(complete, case) is None


def test_a_partial_failure_names_only_what_was_missing():
    case = {
        "question": "q",
        "expected": [
            {"file": "app.py", "line": 5},
            {"file": "utils/helpers.py", "line": 1},
        ],
    }
    problem = _check([Citation(file="app.py", line=5)], case)
    assert problem is not None
    assert "utils/helpers.py:1" in problem
    assert "app.py:5" in problem  # only as part of "got", not as missing
    assert problem.index("utils/helpers.py:1") < problem.index("got")


def test_expected_any_keeps_the_at_least_one_semantics():
    case = {
        "question": "q",
        "expected_any": [
            {"file": "app.py", "line": 5},
            {"file": "utils/helpers.py", "line": 1},
        ],
    }
    assert _check([Citation(file="app.py", line=5)], case) is None


def test_expected_any_fails_when_none_of_them_appear():
    case = {"question": "q", "expected_any": [{"file": "app.py", "line": 5}]}
    problem = _check([Citation(file="other.py", line=5)], case)
    assert problem is not None
    assert "expected one of" in problem


def test_the_line_tolerance_still_applies():
    case = {"question": "q", "expected": [{"file": "app.py", "line": 5}]}
    assert _check([Citation(file="app.py", line=9)], case) is None   # within +/-5
    assert _check([Citation(file="app.py", line=99)], case) is not None


def test_expected_and_expected_any_compose():
    """A case may demand one citation outright and one of several alternatives."""
    case = {
        "question": "q",
        "expected": [{"file": "app.py", "line": 5}],
        "expected_any": [{"file": "a.py", "line": 1}, {"file": "b.py", "line": 1}],
    }
    assert _check([Citation(file="app.py", line=5)], case) is not None  # no any-hit
    assert _check(
        [Citation(file="app.py", line=5), Citation(file="b.py", line=1)], case
    ) is None


# -- provenance downgrades (M13) --------------------------------------------
#
# The rollout decision needs a number: how often does provenance fire, and does
# it fire on cases the model got RIGHT? Without that the flip is a guess.


def test_the_report_carries_a_downgrade_count():
    report = EvalReport(total=1, passed=1, failures=[], downgrades=3)
    assert report.downgrades == 3


def test_downgrades_do_not_affect_report_equality():
    """compare=False, for the same reason traces has it: the exact-equality
    assertions elsewhere in this file must keep working."""
    assert EvalReport(total=1, passed=1, failures=[], downgrades=7) == EvalReport(
        total=1, passed=1, failures=[]
    )


def _provenance_factory(replies: list[LLMReply]):
    def factory() -> Agent:
        return Agent(
            llm=FakeLLM(list(replies)),
            tools=[],
            system_prompt="t",
            repo_root=FIXTURE,
            grounding_gate=GroundingGate(FIXTURE, require_provenance=True),
            allow_retry=False,
        )

    return factory


def test_the_runner_counts_downgrades_across_cases(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    # utils/helpers.py:1 is real, and no tool was ever called — so with
    # provenance on it is a downgrade, and the case fails for lack of a citation.
    factory = _provenance_factory([LLMReply(text="greet is at utils/helpers.py:1.")])
    report = run_eval(yaml_path, factory, repeat=2)
    assert report.downgrades == 2
    assert report.passed == 0


def test_a_clean_run_reports_zero_downgrades(tmp_path: Path):
    yaml_path = tmp_path / "eval.yaml"
    yaml_path.write_text(ONE_CASE, encoding="utf-8")
    factory = make_factory([[LLMReply(text="greet is at utils/helpers.py:1.")]])
    report = run_eval(yaml_path, factory)
    assert report.downgrades == 0
    assert report.passed == 1


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


# -- round-count budget (M14) ------------------------------------------------

from pyrrhon.evals.grounding import _check_rounds  # noqa: E402


def test_a_case_can_cap_the_number_of_model_rounds():
    assert _check_rounds({"max_rounds": 2}, rounds=2) is None
    problem = _check_rounds({"max_rounds": 2}, rounds=4)
    assert problem is not None and "4" in problem


def test_a_case_without_a_cap_never_fails_on_rounds():
    assert _check_rounds({}, rounds=99) is None


def test_a_zero_intervention_rate_says_it_cannot_be_compared():
    """M17's restated S2S criterion, made mechanical.

    M16e's criterion was "the intervention rate falls substantially". It was
    0.0% in BOTH arms, so it had no headroom to fall through — and that was
    discovered only after a day's token budget had been spent, and only by
    reading two reports side by side. The runner says it now, at the moment
    the number is printed.
    """
    from pyrrhon.evals.grounding import measurability_note

    silent = GateCounters(checks=26, promoted=26)
    note = measurability_note(silent)
    assert "CANNOT tell whether" in note
    assert "evals/strangers/README.md" in note


def test_a_gate_that_intervened_needs_no_warning():
    from pyrrhon.evals.grounding import measurability_note

    assert measurability_note(GateCounters(checks=26, intervened=1, promoted=25, stripped=1)) == ""


def test_too_few_checks_is_its_own_answer():
    """A set that produced two citations has not tested the gate, it has just
    been short. Saying "the gate never intervened" about that is a stronger
    claim than the run can support."""
    from pyrrhon.evals.grounding import measurability_note

    note = measurability_note(GateCounters(checks=2, promoted=2))
    assert "too few" in note
