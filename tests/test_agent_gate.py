from pathlib import Path

from pyrrhon.core.agent.loop import Agent
from pyrrhon.core.events import Citation, SpeechChunk
from pyrrhon.core.grounding.gate import LINE_UNSEEN_HEDGE, GroundingGate
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.tools.repo import ReadFileTool
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def make_agent(replies, *, allow_retry: bool = True) -> tuple[Agent, FakeLLM]:
    fake = FakeLLM(replies)
    agent = Agent(
        llm=fake,
        tools=[ReadFileTool(FIXTURE)],
        system_prompt="You are a test agent.",
        repo_root=FIXTURE,
        grounding_gate=GroundingGate(FIXTURE),
        allow_retry=allow_retry,
    )
    return agent, fake


async def collect(agent: Agent, history: list[dict], text: str) -> list:
    return [event async for event in agent.run_turn(history, text)]


async def test_verified_reply_passes_gate_without_retry():
    agent, fake = make_agent([LLMReply(text="greet is at utils/helpers.py:1.")])
    events = await collect(agent, [], "where is greet?")
    assert SpeechChunk(text="greet is at .") in events  # coordinate stripped
    assert Citation(file="utils/helpers.py", line=1) in events
    assert len(fake.calls) == 1  # verified — no retry round-trip


async def test_unverified_reply_triggers_exactly_one_retry():
    agent, fake = make_agent(
        [
            LLMReply(text="greet is at bogus/nowhere.py:7."),
            LLMReply(text="Correction: greet is at utils/helpers.py:1."),
        ]
    )
    history: list[dict] = []
    events = await collect(agent, history, "where is greet?")

    assert len(fake.calls) == 2
    retry_messages = fake.calls[1]["messages"]
    # The retry sees its own draft, then a user-role correction naming the failure:
    assert retry_messages[-2] == {
        "role": "assistant",
        "content": "greet is at bogus/nowhere.py:7.",
    }
    assert retry_messages[-1]["role"] == "user"
    assert "bogus/nowhere.py:7" in retry_messages[-1]["content"]
    assert fake.calls[1]["tools"] is None  # single round-trip, no new tool loop

    assert SpeechChunk(text="Correction: greet is at .") in events
    assert Citation(file="utils/helpers.py", line=1) in events
    # The draft and correction never entered the caller's history:
    assert [m["role"] for m in history] == ["system", "user", "assistant"]
    # M15a: history records what was DELIVERED, and the delivered prose no
    # longer carries the coordinate — it ships as the Citation event above.
    assert history[-1] == {
        "role": "assistant",
        "content": "Correction: greet is at .",
    }


async def test_retry_result_is_gated_without_second_retry():
    agent, fake = make_agent(
        [
            LLMReply(text="see bogus.py:3."),
            LLMReply(text="still bogus: other/fake.py:9."),
        ]
    )
    events = await collect(agent, [], "where?")
    assert len(fake.calls) == 2  # exactly one retry, never two
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert "other/fake.py:9" not in speech[-1].text
    assert speech[-1].text.endswith("I couldn't verify that location.")
    assert not any(isinstance(e, Citation) for e in events)


async def test_allow_retry_false_strips_immediately():
    agent, fake = make_agent(
        [LLMReply(text="see bogus.py:3 for details.")], allow_retry=False
    )
    events = await collect(agent, [], "where?")
    assert len(fake.calls) == 1  # speech path: no retry round-trip, ever
    speech = [e for e in events if isinstance(e, SpeechChunk)]
    assert speech[-1].text == "see for details. I couldn't verify that location."


async def test_no_gate_keeps_m0_behavior():
    fake = FakeLLM([LLMReply(text="see bogus.py:3.")])
    agent = Agent(llm=fake, tools=[], system_prompt="t", repo_root=FIXTURE)
    events = [event async for event in agent.run_turn([], "hi")]
    assert events == [SpeechChunk(text="see bogus.py:3.")]  # ungated, uncited


# -- provenance end-to-end (M13) --------------------------------------------


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "\n".join(f"line {n}" for n in range(1, 51)), encoding="utf-8"
    )
    return tmp_path


def _provenance_agent(tmp_path: Path, replies) -> Agent:
    return Agent(
        llm=FakeLLM(replies),
        tools=[ReadFileTool(tmp_path)],
        system_prompt="s",
        repo_root=tmp_path,
        grounding_gate=GroundingGate(tmp_path, require_provenance=True),
    )


async def _spoken(agent: Agent, history: list[dict], text: str) -> str:
    chunks = [
        event.text
        async for event in agent.run_turn(history, text)
        if isinstance(event, SpeechChunk)
    ]
    return " ".join(chunks)


async def _cited(agent: Agent, history: list[dict], text: str) -> list[tuple[str, int]]:
    """M15a: a verified reference proves itself as a Citation event, not as
    speech — the coordinate is stripped from prose and rendered separately."""
    return [
        (event.file, event.line)
        async for event in agent.run_turn(history, text)
        if isinstance(event, Citation)
    ]


async def test_a_line_the_model_read_is_cited(tmp_path: Path):
    agent = _provenance_agent(
        _repo(tmp_path),
        [
            LLMReply(tool_calls=(ToolCall(id="1", name="read_file", arguments={"path": "app.py"}),)),
            LLMReply(text="The handler is at app.py:12."),
        ],
    )
    assert await _cited(agent, [], "where is it?") == [("app.py", 12)]


async def test_a_line_the_model_never_read_is_downgraded(tmp_path: Path):
    """No tool call at all — the model simply asserts a plausible location."""
    agent = _provenance_agent(
        _repo(tmp_path), [LLMReply(text="The handler is at app.py:12.")]
    )
    spoken = await _spoken(agent, [], "where is it?")
    assert "app.py:12" not in spoken
    assert LINE_UNSEEN_HEDGE in spoken


async def test_reading_a_different_file_does_not_license_the_citation(tmp_path: Path):
    """Evidence is per-file, not per-turn: opening one file proves nothing
    about a line in another."""
    repo = _repo(tmp_path)
    (repo / "other.py").write_text("x = 1\n", encoding="utf-8")
    agent = _provenance_agent(
        repo,
        [
            LLMReply(tool_calls=(ToolCall(id="1", name="read_file", arguments={"path": "other.py"}),)),
            LLMReply(text="The handler is at app.py:12."),
        ],
    )
    spoken = await _spoken(agent, [], "where is it?")
    assert "app.py:12" not in spoken
    assert LINE_UNSEEN_HEDGE in spoken


async def test_a_line_outside_the_window_the_model_read_is_downgraded(tmp_path: Path):
    """Read lines 1-20, cite line 44: the file was opened, that line was not."""
    agent = _provenance_agent(
        _repo(tmp_path),
        [
            LLMReply(
                tool_calls=(
                    ToolCall(
                        id="1",
                        name="read_file",
                        arguments={"path": "app.py", "start_line": 1, "end_line": 20},
                    ),
                )
            ),
            LLMReply(text="See app.py:12 and app.py:44."),
        ],
    )
    spoken = await _spoken(agent, [], "where is it?")
    # 12 verified (a Citation event); 44 was downgraded and hedged. Neither
    # coordinate is spoken.
    assert "app.py:12" not in spoken
    assert "app.py:44" not in spoken
    assert LINE_UNSEEN_HEDGE in spoken


async def test_the_ledger_is_fresh_every_turn(tmp_path: Path):
    """Evidence from turn 1 must not license a citation in turn 2 — the model
    may be talking about a file that changed, and 'I read it earlier' is
    exactly the reasoning that produces stale-line citations."""
    agent = _provenance_agent(
        _repo(tmp_path),
        [
            LLMReply(tool_calls=(ToolCall(id="1", name="read_file", arguments={"path": "app.py"}),)),
            LLMReply(text="Read it."),
            LLMReply(text="The handler is at app.py:12."),
        ],
    )
    history: list[dict] = []
    await _spoken(agent, history, "read it")
    spoken = await _spoken(agent, history, "now where is it?")
    assert "app.py:12" not in spoken
    assert LINE_UNSEEN_HEDGE in spoken


async def test_provenance_defaults_off_so_an_unread_line_still_cites(tmp_path: Path):
    """The rollout guarantee: until the eval says otherwise, a build_agent-shaped
    gate behaves exactly as it did before M13."""
    repo = _repo(tmp_path)
    agent = Agent(
        llm=FakeLLM([LLMReply(text="The handler is at app.py:12.")]),
        tools=[ReadFileTool(repo)],
        system_prompt="s",
        repo_root=repo,
        grounding_gate=GroundingGate(repo),
    )
    assert await _cited(agent, [], "where is it?") == [("app.py", 12)]
