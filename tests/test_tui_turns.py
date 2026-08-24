from pathlib import Path

from textual.widgets import Markdown

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.events import Citation, SpeechChunk
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.prompt import Prompt
from tests.helpers import FakeLLM, StreamingFakeLLM


def make_app(replies, repo_root: Path) -> tuple[PyrrhonApp, FakeLLM]:
    # repo_root must be disposable (the `sample_repo` fixture or tmp_path):
    # mounting the TUI warms the symbol index, which writes .pyrrhon/cache.db.
    fake = FakeLLM(replies)
    agent = build_agent(repo_root, llm=fake, home=repo_root.parent)
    return PyrrhonApp(repo_root=repo_root, agent=agent), fake


async def submit(app: PyrrhonApp, pilot, text: str) -> None:
    app.query_one("#prompt", Prompt).value = text
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_turn_streams_speech_and_records_the_citation(sample_repo: Path):
    replies = [
        LLMReply(
            tool_calls=(
                ToolCall(id="c1", name="read_file", arguments={"path": "utils/helpers.py"}),
            )
        ),
        LLMReply(text="greet is defined at utils/helpers.py:1."),
    ]
    app, fake = make_app(replies, sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "where is greet defined?")
        assert app.history[-1] == {
            "role": "assistant",
            "content": "greet is defined at .",
        }
        assert app.last_citation == Citation(file="utils/helpers.py", line=1)
        prompt = app.query_one("#prompt", Prompt)
        assert not prompt.disabled and prompt.has_focus  # ready for the next turn


async def test_slash_command_short_circuits_the_agent(sample_repo: Path):
    # any LLM call would raise inside FakeLLM
    app, fake = make_app([], sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/help")
        assert fake.calls == []  # the LLM was never touched
        assert app.history == []  # commands are not conversation
        assert "/model" in app.last_command_response


async def test_unknown_command_is_reported(sample_repo: Path):
    app, fake = make_app([], sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/definitely-not-a-command")
        assert "Unknown command" in app.last_command_response


async def test_init_via_tui(tmp_path: Path):
    app, fake = make_app([], repo_root=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/init")
        assert (tmp_path / ".pyrrhon" / "soul.md").is_file()
        assert "soul file created" in app.last_command_response


async def test_turn_failure_reports_error_and_recovers(sample_repo: Path):
    # first chat() call raises inside FakeLLM
    app, fake = make_app([], sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "hello")
        prompt = app.query_one("#prompt", Prompt)
        assert not prompt.disabled and prompt.has_focus  # session survived the failed turn


# -- Phase 2: the transcript -----------------------------------------------


async def test_a_three_sentence_answer_is_one_document(sample_repo: Path):
    """The regression test for defect 2.

    The core selects the sentence splitter whenever voice is active, and the
    TUI used to call Markdown() once per chunk — so a three-sentence answer
    was three stacked documents, each with its own padding. StreamingFakeLLM
    is what puts the agent on the streaming path, which is where the splitter
    actually runs; the chunk count is asserted so this proves the screen
    coalesced three chunks rather than that the splitter stopped splitting.
    """
    body = "First sentence. Second sentence. Third sentence."
    llm = StreamingFakeLLM([(list(body), LLMReply(text=body))])
    agent = build_agent(sample_repo, llm=llm, home=sample_repo.parent)
    agent.voice_active = True
    app = PyrrhonApp(repo_root=sample_repo, agent=agent)

    chunks: list[str] = []
    original = app._render_event

    def counting(event):
        if isinstance(event, SpeechChunk):
            chunks.append(event.text)
        original(event)

    app._render_event = counting

    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "tell me three things")
        await pilot.pause()
        assert len(chunks) >= 3, f"the splitter should still split: {chunks}"
        speech = list(app.query("AssistantRow.speech"))
        assert len(speech) == 1, "one turn, one prose row"
        assert len(list(speech[0].query(Markdown))) == 1, "one markdown document"


async def test_two_tool_calls_produce_two_resolved_rows(sample_repo: Path):
    """Defect 6: ToolCallFinished was dropped by every screen channel, so a
    user learned a tool started and never learned whether it worked."""
    from pyrrhon.tui.messages import ToolRow

    replies = [
        LLMReply(tool_calls=(
            ToolCall(id="c1", name="read_file", arguments={"path": "utils/helpers.py"}),
            ToolCall(id="c2", name="read_file", arguments={"path": "main.py"}),
        )),
        LLMReply(text="Both read."),
    ]
    app, fake = make_app(replies, sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "read both files")
        await pilot.pause()
        rows = list(app.query(ToolRow))
        assert len(rows) == 2, "one row per call, not a second row on finish"
        assert all(row.state != "running" for row in rows), "both resolved"


async def test_a_hedge_leaves_the_prose_for_the_warning_rail(sample_repo: Path):
    """A hedge is a different epistemic claim from the prose it trails."""
    from pyrrhon.core.grounding.gate import HEDGE
    from pyrrhon.tui.renderer import split_hedge

    prose, hedge = split_hedge(f"It lives in the loop. {HEDGE}")
    assert prose == "It lives in the loop."
    assert hedge == HEDGE
    assert split_hedge("No hedge here.") == ("No hedge here.", "")


async def test_the_transcript_reads_in_the_order_the_events_arrived(sample_repo: Path):
    """Widget.mount() is asynchronous, and that broke the order.

    The working row was still pending when the turn's first event arrived, so
    it was not `is_mounted`, the tool row skipped the `before=` insertion and
    was appended after the spinner — which left it at the bottom of the turn
    once the spinner was removed. A user read the citation before the tool
    call that produced it.
    """
    from pyrrhon.tui.messages import AssistantRow, CitationRow, ToolRow, UserRow

    replies = [
        LLMReply(tool_calls=(
            ToolCall(id="c1", name="read_file", arguments={"path": "utils/helpers.py"}),
        )),
        LLMReply(text="greet is defined at utils/helpers.py:1."),
    ]
    app, fake = make_app(replies, sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "where is greet defined?")
        await pilot.pause()
        kinds = [
            type(child).__name__
            for child in app.query_one("#transcript").children
            if isinstance(child, (UserRow, ToolRow, AssistantRow, CitationRow))
        ]
        assert kinds.index("UserRow") < kinds.index("ToolRow")
        assert kinds.index("ToolRow") < kinds.index("CitationRow")


async def test_rows_are_as_tall_as_their_content_and_no_taller(sample_repo: Path):
    """The "huge gap" between a question and its answer.

    The rail was `height: 1fr` inside a `height: auto` row, which is circular:
    Textual resolved the fr against the viewport rather than the sibling, so
    every row rendered the full height of the screen. Three rows filled a
    terminal and the transcript looked like it had enormous blank gaps.
    """
    from pyrrhon.tui.messages import Row

    replies = [LLMReply(text="One short line.")]
    app, fake = make_app(replies, sample_repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await submit(app, pilot, "hi")
        await pilot.pause()
        for row in app.query(Row):
            assert row.outer_size.height <= 6, (
                f"{type(row).__name__} is {row.outer_size.height} lines tall"
            )


async def test_a_long_artifact_arrives_folded(sample_repo: Path):
    """M14's orientation brief is a hundred lines of symbol counts, and it
    used to land on top of the splash as the first thing a user ever saw."""
    from pyrrhon.tui.messages import AssistantRow, BriefRow, artifact_row

    wall = chr(10).join(f"line {i}" for i in range(60))
    folded = artifact_row(wall)
    assert isinstance(folded, BriefRow)
    assert folded.body().collapsed, "folded until asked for"

    short = artifact_row("Just a sentence.")
    assert isinstance(short, AssistantRow), "a short artifact still reads inline"


# -- the joiner, the scroll offset, and the row a command answer wears ------


async def test_a_list_after_a_paragraph_survives_the_chunk_boundary(sample_repo: Path):
    """The core splits text into markdown *blocks* and strips each one.

    `loop.py:_pop_blocks` cuts at a blank line and hands over the block with
    the blank line gone, joining history with "\n\n" to put it back. The TUI
    concatenated the chunks with nothing, so the separator vanished on screen
    only: a paragraph fused with the list that followed it and rendered as one
    run-on line, taking every heading, table and list after the first block
    with it. The document is what this asserts, because that is what the
    reader sees.
    """
    body = "A paragraph here.\n\n- first item\n- second item\n"
    llm = StreamingFakeLLM([(list(body), LLMReply(text=body))])
    agent = build_agent(sample_repo, llm=llm, home=sample_repo.parent)
    assert not agent.voice_active, "the text splitter is the one under test"
    app = PyrrhonApp(repo_root=sample_repo, agent=agent)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "describe it")
        await pilot.pause()
        row = app.query_one("AssistantRow.speech")
        source = row.markdown.source
        assert "A paragraph here." in source
        assert "\n\n- first item" in source, (
            f"the block separator was lost: {source!r}"
        )


async def test_the_transcript_reads_from_the_top(sample_repo: Path):
    """VerticalScroll.anchor() left a negative scroll offset here.

    Its scroll_end runs before the layout that would bound it, and the offset
    it settles on is never revised — so the transcript sat at
    scroll_y = -(viewport height) and every row rendered bottom-aligned under
    a screen-high blank gap. Short content must start at the top.
    """
    from textual.containers import VerticalScroll

    app, _ = make_app([], sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/help")
        await pilot.pause()
        transcript = app.query_one("#transcript", VerticalScroll)
        assert transcript.scroll_offset.y == 0, "a short transcript never scrolls"
        first = transcript.children[0]
        assert first.region.y == transcript.region.y, (
            "the first row sits at the top of the transcript, not the bottom"
        )


async def test_a_command_answer_is_not_dressed_as_a_warning(sample_repo: Path):
    """`/help` came back as a NoticeRow — the ⚠ rail in hedge amber, which is
    the styling that means "Pyrrhon could not verify this". Listing the
    command table carries no such doubt; a failure still does."""
    from pyrrhon.tui.messages import CommandRow, NoticeRow

    app, _ = make_app([], sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/help")
        await pilot.pause()
        assert len(list(app.query(CommandRow))) == 1
        assert not list(app.query(NoticeRow)), "nothing here is a warning"

        await submit(app, pilot, "/model nonsense")
        await pilot.pause()
        assert len(list(app.query(NoticeRow))) == 1, "a failure still warns"


async def test_exit_asks_the_app_to_leave(sample_repo: Path):
    """/exit is a row in the command table, so it reaches the TUI through the
    same dispatch every other command does."""
    app, _ = make_app([], sample_repo)
    asked: list[int] = []
    app.request_exit = lambda: asked.append(1)
    async with app.run_test(size=(120, 40)) as pilot:
        await submit(app, pilot, "/exit")
        await pilot.pause()
        assert asked == [1]
        assert "Leaving" in app.last_command_response
