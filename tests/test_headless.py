"""The headless channel: one question in, one answer out.

What is worth pinning here is the contract a script depends on, not the prose.
Three things are that contract. stdout carries the answer and nothing else,
because a caller pipes it. Blocks are rejoined with the separator the core
split on, because the alternative silently fuses a paragraph into the list
below it. And empty input is an error rather than an empty answer, because a
caller that pipes in nothing has a bug upstream and a blank line hides it.
"""

from __future__ import annotations

import io
import json

import pytest

from pyrrhon.cli import main as cli_main
from pyrrhon.core.events import (
    Citation,
    ProviderRetrying,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
)
from pyrrhon.core.telemetry import TurnTrace
from pyrrhon.headless import HeadlessRenderer, _report, read_prompt


class FakeSession:
    def __init__(self, trace: TurnTrace | None):
        self.last_turn_trace = trace
        self.last_turn_latency_ms = 42.0


def test_blocks_rejoin_with_the_separator_the_core_split_on():
    """The core hands over one markdown BLOCK per chunk on the text path, so
    concatenating with nothing fuses a paragraph into the list that follows
    it. The TUI paid for this once; the joiner has to be reapplied by whoever
    concatenates, and there is no recovering it downstream."""
    renderer = HeadlessRenderer(progress=False)
    renderer.render(SpeechChunk(text="The answer."))
    renderer.render(SpeechChunk(text="- one\n- two"))
    assert renderer.answer == "The answer.\n\n- one\n- two"


def test_only_the_answer_reaches_stdout(capsys):
    """Progress is for a human watching a slow job; a pipe must never see it.
    stderr is where that distinction is already conventional."""
    renderer = HeadlessRenderer(progress=True)
    renderer.render(ToolCallStarted(name="grep", args={"pattern": "x"}))
    renderer.render(ProviderRetrying(delay_seconds=3.0, reason="rate limit"))
    renderer.render(SpeechChunk(text="hello"))
    _report(renderer, FakeSession(None), as_json=False)
    captured = capsys.readouterr()
    assert captured.out.strip() == "hello"
    assert "grep" in captured.err
    assert "retrying" in captured.err


def test_progress_off_writes_nothing_anywhere_but_stdout(capsys):
    """A redirected stderr is a log file, and a log full of tool lines is what
    a non-interactive caller asked not to have."""
    renderer = HeadlessRenderer(progress=False)
    renderer.render(ToolCallStarted(name="grep", args={"pattern": "x"}))
    renderer.render(SpeechChunk(text="hello"))
    _report(renderer, FakeSession(None), as_json=False)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip() == "hello"


def test_json_carries_the_citations_a_script_cannot_click(capsys):
    """The screen channels render a citation as a clickable row because a
    human clicks it. A script cannot, so the structured mode is where a
    citation stops being decoration and becomes data."""
    renderer = HeadlessRenderer(progress=False)
    renderer.render(SpeechChunk(text="It caches on write."))
    renderer.render(Citation(file="pkg/mod.py", line=12))
    trace = TurnTrace()
    trace.stop_reason = "answered"
    round_trace = trace.begin_round()
    round_trace.tools.append(object())  # only the count is read
    _report(renderer, FakeSession(trace), as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer"] == "It caches on write."
    assert payload["citations"] == [{"file": "pkg/mod.py", "line": 12}]
    assert payload["rounds"] == 1
    assert payload["tool_calls"] == 1
    assert payload["stop_reason"] == "answered"


def test_json_survives_a_turn_that_produced_no_trace(capsys):
    """A turn that died before its first round leaves last_turn_trace None.
    A caller parsing JSON must still get JSON — a crash here would turn a
    provider outage into a broken pipe two layers away."""
    _report(HeadlessRenderer(progress=False), FakeSession(None), as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["rounds"] == 0
    assert payload["stop_reason"] is None


def test_a_finished_tool_says_nothing():
    """Inherited from ConsoleRenderer's reasoning, and worth stating: the
    started line already named what is running, so a finished line would
    double every entry in the log for no new information."""
    renderer = HeadlessRenderer(progress=True)
    renderer.render(ToolCallFinished(name="grep", result_preview="x"))
    assert renderer.blocks == []


def test_the_prompt_comes_from_stdin_whole(monkeypatch):
    """Read whole rather than by line: a question can be a paragraph, and
    splitting on newlines would silently answer only its first sentence."""
    monkeypatch.setattr("sys.stdin", io.StringIO("why does this\ncache on write?\n"))
    assert read_prompt(None) == "why does this\ncache on write?"


def test_an_argument_wins_over_stdin(monkeypatch):
    """`pyrrhon -p "q" < file` is a real invocation and the argument is the
    explicit one, so stdin is not even read."""
    monkeypatch.setattr("sys.stdin", io.StringIO("ignored"))
    assert read_prompt("  asked  ") == "asked"


def test_empty_input_is_an_error_not_an_empty_answer(monkeypatch, capsys):
    """Exit 2, argparse's own usage code, because this IS a usage error."""
    monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
    from pyrrhon.headless import main_headless

    with pytest.raises(SystemExit) as exit_info:
        main_headless(".", None)
    assert exit_info.value.code == 2
    assert "no question given" in capsys.readouterr().err


def test_print_beats_text_and_voice_at_the_cli(monkeypatch):
    """--print is a different KIND of run, not a third screen, so it is
    checked before the channel flags. Voice on a channel with no microphone
    and no listener would be a silent no-op."""
    seen: dict = {}
    monkeypatch.setattr(
        "pyrrhon.headless.main_headless",
        lambda repo, prompt, **kwargs: seen.update(repo=repo, prompt=prompt, **kwargs),
    )
    cli_main(["--text", "--voice", "-p", "what is this", "some/repo"])
    assert seen == {
        "repo": "some/repo",
        "prompt": "what is this",
        "trust_repo": False,
        "as_json": False,
    }


def test_bare_print_falls_through_to_stdin(monkeypatch):
    """`--print` with no argument is the piping form, so the CLI passes None
    and lets read_prompt reach for stdin."""
    seen: dict = {}
    monkeypatch.setattr(
        "pyrrhon.headless.main_headless",
        lambda repo, prompt, **kwargs: seen.update(prompt=prompt, **kwargs),
    )
    cli_main(["--print", "--json"])
    assert seen["prompt"] is None
    assert seen["as_json"] is True
