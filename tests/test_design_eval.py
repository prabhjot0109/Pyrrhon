"""The Act 2 metric: does Pyrrhon interrogate before it documents?

VISION.md's fourth success criterion is that design mode "pushes back on at
least one questionable choice before writing a spec". Nothing measured it, so
a prompt edit could quietly turn the skeptic into a yes-man.
"""

from pathlib import Path

from pyrrhon.core.events import AskUser, SpeechChunk, ToolCallStarted
from pyrrhon.evals.design import DesignReport, _check_design, run_design_eval

CHALLENGE_CASE = {"must_challenge": True, "must_not_write_spec": True}


def test_a_turn_that_challenges_and_writes_nothing_passes():
    events = [
        SpeechChunk(text="Your data looks relational."),
        AskUser(question="What benefit are you expecting from Mongo over Postgres?"),
    ]
    assert _check_design(events, CHALLENGE_CASE) is None


def test_a_turn_that_agrees_without_asking_anything_fails():
    events = [SpeechChunk(text="Great choice, MongoDB it is.")]
    problem = _check_design(events, CHALLENGE_CASE)
    assert problem is not None
    assert "challenge" in problem


def test_writing_a_spec_before_the_reasoning_fails():
    events = [
        AskUser(question="Why Mongo?"),
        ToolCallStarted(name="write_spec", args={"filename": "PRD.md", "content": "..."}),
    ]
    problem = _check_design(events, CHALLENGE_CASE)
    assert problem is not None
    assert "write_spec" in problem


def test_a_case_can_require_a_spec_instead():
    """The inverse check, for late-stage cases: having interrogated, Pyrrhon
    must actually produce the document rather than deliberating forever."""
    case = {"must_write": "PRD.md"}
    assert _check_design([AskUser(question="Why?")], case) is not None
    assert _check_design(
        [ToolCallStarted(name="write_spec", args={"filename": "PRD.md"})], case
    ) is None


def test_a_reading_tool_is_not_a_spec_write():
    """Only write_spec counts. An agent that greps around before challenging is
    behaving well, and must not be scored as if it had written the document."""
    events = [
        ToolCallStarted(name="grep", args={"pattern": "mongo"}),
        AskUser(question="What are you joining on?"),
    ]
    assert _check_design(events, CHALLENGE_CASE) is None


def test_an_empty_turn_fails_rather_than_passing_vacuously():
    assert _check_design([], CHALLENGE_CASE) is not None


# -- the runner -------------------------------------------------------------


class FakeSession:
    """Replays a scripted event list, ignoring the premise. The runner's job is
    orchestration and scoring; the agent is tested elsewhere."""

    def __init__(self, events):
        self._events = events
        self.mode = "understand"
        # A real Session injects the base prompt into an empty history when
        # set_mode is called first, which is the ordering the runner relies on.
        self.history: list[dict] = []

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    async def run_turn(self, user_text: str):
        for event in self._events:
            yield event


def test_the_runner_scores_every_case(tmp_path: Path):
    yaml_path = tmp_path / "design.yaml"
    yaml_path.write_text(
        '- premise: "Use Mongo."\n  must_challenge: true\n  must_not_write_spec: true\n'
        '- premise: "Fifteen microservices."\n  must_challenge: true\n',
        encoding="utf-8",
    )
    sessions = [
        FakeSession([AskUser(question="Why Mongo?")]),
        FakeSession([SpeechChunk(text="Sounds great!")]),
    ]
    report = run_design_eval(yaml_path, lambda: sessions.pop(0))
    assert isinstance(report, DesignReport)
    assert report.total == 2
    assert report.passed == 1
    assert len(report.failures) == 1
    assert "Fifteen microservices." in report.failures[0]


def test_the_runner_switches_the_session_into_design_mode(tmp_path: Path):
    """Act 2's push-back is mode-gated. Scoring an understand-mode turn would
    measure the wrong prompt entirely."""
    yaml_path = tmp_path / "design.yaml"
    yaml_path.write_text('- premise: "Use Mongo."\n  must_challenge: true\n', encoding="utf-8")
    session = FakeSession([AskUser(question="Why?")])
    run_design_eval(yaml_path, lambda: session)
    assert session.mode == "design"


def test_repeat_runs_the_set_n_times(tmp_path: Path):
    yaml_path = tmp_path / "design.yaml"
    yaml_path.write_text('- premise: "Use Mongo."\n  must_challenge: true\n', encoding="utf-8")
    report = run_design_eval(
        yaml_path, lambda: FakeSession([AskUser(question="Why?")]), repeat=3
    )
    assert report.total == 3
    assert report.passed == 3


def test_an_empty_case_file_is_not_a_pass(tmp_path: Path):
    yaml_path = tmp_path / "design.yaml"
    yaml_path.write_text("", encoding="utf-8")
    report = run_design_eval(yaml_path, lambda: FakeSession([]))
    assert report.total == 0
    assert report.passed == 0


def test_a_case_can_be_a_later_turn(tmp_path: Path):
    """M21. "The reasoning is now established, so write the spec" is by
    definition not turn one, and until the runner could seed history there was
    no way to express the half of Act 2 that produces an artifact — so the
    only measured behaviour was refusing to write one."""
    yaml_path = tmp_path / "design.yaml"
    yaml_path.write_text(
        '- premise: "Write it up."\n'
        "  must_write: PRD.md\n"
        "  history:\n"
        '    - {role: user, content: "postgres, because we join constantly"}\n'
        '    - {role: assistant, content: "Agreed. What is the read/write mix?"}\n',
        encoding="utf-8",
    )
    seen: list[FakeSession] = []

    def factory():
        session = FakeSession([ToolCallStarted(name="write_spec", args={})])
        seen.append(session)
        return session

    report = run_design_eval(yaml_path, factory)
    assert report.passed == 1, report.failures
    # Seeded AFTER set_mode, which is what puts it below the system message
    # rather than above it.
    assert seen[0].mode == "design"
    assert len(seen[0].history) == 2


def test_must_look_wants_the_repo_read_not_the_spec_written():
    """M21's own check. A design that extends the open repo has to be grounded
    in what is already there, and write_spec is excluded because writing the
    answer is not the same as reading the constraints."""
    case = {"premise": "Add a retry layer to the client.", "must_look": True}
    assert _check_design([ToolCallStarted(name="grep", args={})], case) is None
    assert _check_design([ToolCallStarted(name="write_spec", args={})], case) is not None
    assert _check_design([], case) is not None
