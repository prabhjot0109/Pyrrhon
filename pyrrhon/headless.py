"""The non-interactive channel: one question in, one answer out.

Until now the only way into the agent was a terminal a human was sitting at,
or one of the two eval harnesses. That makes Pyrrhon unusable from a script, a
CI job, a git hook, or a pipe, and it is also why every runtime pass the M16
milestones owe has to be driven by hand.

Three decisions shape the output, and each one is about who is reading it.

**The answer goes to stdout and nothing else does.** A caller piping this into
`jq`, `grep` or a file wants the answer, not a banner. Tool progress therefore
goes to stderr, where a human watching a slow CI job still sees work happening
and a pipe never sees a byte of it.

**Citations are structured or absent, never decorative.** The screen channels
render a citation as a clickable row because a human clicks it. A script
cannot, so plain mode prints the prose alone and `--json` carries the
citations as data beside it.

**The trust gate refuses rather than prompts.** `load_channel_plugins` already
refuses when stdin is not a terminal, but a headless run started from an
interactive shell would otherwise stop dead on a consent prompt nobody is
watching. So this channel answers every grant request with "no" and says so on
stderr; `--trust-repo` remains the one way to say yes, which is exactly the
automation escape hatch it was built to be.
"""

from __future__ import annotations

import json
import sys

from pyrrhon.bootstrap import start_channel, warm_index_in_background
from pyrrhon.channels import EventRenderer
from pyrrhon.core.events import (
    Citation,
    ProviderRetrying,
    SpeechChunk,
    ToolCallStarted,
)
from pyrrhon.core.mcp import MCPManager
from pyrrhon.core.session import Session
from pyrrhon.plugins import LoadedPlugin


class HeadlessRenderer(EventRenderer):
    """Collects the answer; narrates the work on stderr.

    Speech chunks are joined with a blank line rather than concatenated. The
    core hands over one markdown BLOCK per chunk on the text path
    (`loop._pop_blocks` strips each block and rejoins history with a blank
    line), so joining with nothing fuses a paragraph into the list that
    follows it and swallows every heading after the first. The TUI learned
    this the hard way; the joiner has to be reapplied by whoever concatenates.
    """

    def __init__(self, progress: bool = True):
        self.blocks: list[str] = []
        self.citations: list[Citation] = []
        self._progress = progress

    @property
    def answer(self) -> str:
        return "\n\n".join(self.blocks)

    def on_speech(self, event: SpeechChunk) -> None:
        self.blocks.append(event.text)

    def on_citation(self, event: Citation) -> None:
        self.citations.append(event)

    def on_tool_started(self, event: ToolCallStarted) -> None:
        if self._progress:
            print(f"→ {event.name}({event.args})", file=sys.stderr)

    def on_provider_retrying(self, event: ProviderRetrying) -> None:
        if self._progress:
            print(
                f"rate limited — retrying in {event.delay_seconds:.0f}s",
                file=sys.stderr,
            )


def _report(renderer: HeadlessRenderer, session: Session, as_json: bool) -> None:
    """Everything the caller gets, written once at the end.

    Written at the end rather than streamed because a partial answer on stdout
    is worse than no answer for a caller that will act on it: a turn that dies
    at round four has already printed three paragraphs a script would treat as
    the reply. The screen channels stream because a human can see it stop.
    """
    if not as_json:
        print(renderer.answer)
        return
    trace = session.last_turn_trace
    print(
        json.dumps(
            {
                "answer": renderer.answer,
                "citations": [
                    {"file": c.file, "line": c.line} for c in renderer.citations
                ],
                "rounds": len(trace.rounds) if trace else 0,
                "tool_calls": sum(len(r.tools) for r in trace.rounds) if trace else 0,
                "stop_reason": trace.stop_reason if trace else None,
                "latency_ms": session.last_turn_latency_ms,
            },
            indent=2,
        )
    )


def read_prompt(argument: str | None) -> str:
    """The question, from the argument or from stdin.

    Stdin is the piping case (`echo "..." | pyrrhon -p`), and it is read whole
    rather than by line: a question can be a paragraph, and splitting on
    newlines would silently answer only its first sentence.
    """
    if argument:
        return argument.strip()
    return sys.stdin.read().strip()


def run_headless(
    repo: str,
    prompt: str,
    trust_repo: bool = False,
    as_json: bool = False,
    progress: bool = True,
) -> None:
    def _refuse(question: str) -> bool:
        print(
            f"{question}\nrefused: headless runs never grant repo permissions. "
            "Pass --trust-repo if that is what you want.",
            file=sys.stderr,
        )
        return False

    renderer = HeadlessRenderer(progress=progress)

    async def _serve(agent, manager: MCPManager, plugins: list[LoadedPlugin]) -> None:
        warm = warm_index_in_background(agent)  # noqa: F841 - ref held, see repl.py
        agent.on_progress = renderer.render
        session = Session(agent)
        async for event in session.run_turn(prompt):
            renderer.render(event)
        _report(renderer, session, as_json)

    start_channel(
        repo,
        _serve,
        ask=_refuse,
        report=lambda msg: print(msg, file=sys.stderr),
        trust_repo=trust_repo,
    )


def main_headless(
    repo: str,
    prompt_arg: str | None,
    trust_repo: bool = False,
    as_json: bool = False,
) -> None:
    """The CLI's entry point. Empty input is an error, not an empty answer.

    A caller that pipes in nothing has a bug upstream, and answering it with a
    blank line hides that bug one layer further from where it happened.
    """
    prompt = read_prompt(prompt_arg)
    if not prompt:
        print("pyrrhon --print: no question given (argument or stdin)", file=sys.stderr)
        raise SystemExit(2)
    # Progress narration is for a human watching; a redirected stderr is a log
    # file, and a log full of tool lines is what a caller asked not to have.
    run_headless(
        repo,
        prompt,
        trust_repo=trust_repo,
        as_json=as_json,
        progress=sys.stderr.isatty(),
    )
