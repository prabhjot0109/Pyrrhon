from pathlib import Path

from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.events import AskUser, SpeechChunk, ToolCallStarted
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.core.session import MODE_PREFIX, Session
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM

CHALLENGE = (
    "Your data looks relational — users, orders, joins. "
    "What specific benefit are you expecting from Mongo over Postgres here?"
)

PRD_CONTENT = """\
# PRD — Order Service

## Problem
Small merchants need order tracking with reliable payment state.

## Decision: Postgres over MongoDB
Proposed: MongoDB. Challenged: the data is relational (users, orders,
line-item joins). Justification given: the team knows Postgres, and payment
state transitions need transactional integrity. Alternatives considered:
MongoDB (rejected — no benefit identified for relational data), DynamoDB
(rejected — no team experience, same join problem). Decision: Postgres.
"""


async def test_scripted_design_session_challenges_then_writes_prd(tmp_path: Path):
    fake = FakeLLM(
        [
            # Round 1: the model challenges the weakest assumption — no tools.
            LLMReply(text=CHALLENGE),
            # Round 2: justification received → write the PRD...
            LLMReply(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="write_spec",
                        arguments={"filename": "PRD.md", "content": PRD_CONTENT},
                    ),
                )
            ),
            # ...then announce it in speech.
            LLMReply(
                text="I've written the PRD at docs/design/PRD.md — Postgres it is."
            ),
        ]
    )
    agent = build_agent(tmp_path, llm=fake)
    session = Session(agent)
    session.set_mode("design")
    assert {"role": "system", "content": MODE_PREFIX + DESIGN_PROMPT} in session.history

    # --- Round 1: proposal → challenge, and nothing gets written ---
    round1 = [
        e
        async for e in agent.run_turn(
            session.history, "Let's build the order service on MongoDB."
        )
    ]
    spec_calls = [
        e for e in round1 if isinstance(e, ToolCallStarted) and e.name == "write_spec"
    ]
    assert spec_calls == []  # pushback happens BEFORE any artifact exists
    assert not (tmp_path / "docs" / "design" / "PRD.md").exists()
    assert SpeechChunk(text=CHALLENGE) in round1
    assert AskUser(
        question=(
            "What specific benefit are you expecting from Mongo over Postgres here?"
        )
    ) in round1

    # --- Round 2: justification → write_spec → PRD.md on disk ---
    round2 = [
        e
        async for e in agent.run_turn(
            session.history,
            "Fair. The data is relational and payment state needs transactions "
            "— Postgres, and here's the reasoning for the record.",
        )
    ]
    started = [e for e in round2 if isinstance(e, ToolCallStarted)]
    assert started and started[0].name == "write_spec"
    prd = tmp_path / "docs" / "design" / "PRD.md"
    assert prd.read_text(encoding="utf-8") == PRD_CONTENT
    speech = [e for e in round2 if isinstance(e, SpeechChunk)]
    assert "docs/design/PRD.md" in speech[-1].text
