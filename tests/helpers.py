"""Test doubles shared across the suite."""

from __future__ import annotations

from pyrrhon.core.providers.llm import LLMReply


def write_plugin(parent, name: str, manifest: str, files: dict[str, str] | None = None):
    """Create <parent>/<name>/plugin.toml (+ extra files) for plugin tests.

    `parent` is a plugins base dir like <home>/.pyrrhon/plugins. Returns the
    plugin directory as a pathlib.Path.
    """
    plugin_dir = parent / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(manifest, encoding="utf-8")
    for rel, content in (files or {}).items():
        target = plugin_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return plugin_dir


class FakeLLM:
    """Duck-typed stand-in for OpenAICompatLLM: returns scripted replies in order.

    Deliberately exposes chat() and NOT stream(). Agent.run_turn selects the
    streaming path with `hasattr(self.llm, "stream")`, so every suite built on
    this double keeps exercising the whole-reply path.
    """

    def __init__(self, replies: list[LLMReply]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMReply:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self._replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self._replies.pop(0)


class StreamingFakeLLM:
    """Scripted streaming double: each round is (deltas, LLMReply).

    Having stream() is what puts Agent.run_turn on the streaming path, so this
    is the double to reach for when testing either voice sentences or text
    markdown blocks.
    """

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    async def stream(self, messages, tools=None):
        self.calls += 1
        deltas, reply = self._rounds.pop(0)
        for delta in deltas:
            yield ("text", delta)
        yield ("reply", reply)


async def settle(pilot, until, what: str, limit: int = 20) -> None:
    """Pause a Textual test until an effect has actually landed.

    **One `pilot.pause()` drains the message queue once**, and that is not
    enough whenever the work being waited on is deferred (`call_later`), is an
    `async def` hook that awaits something of its own, or is a reactive watcher
    that schedules a second pass. Under load — a full suite on a busy machine —
    a test that acts after a single pause is not testing the sequence it
    describes. It is testing whichever sequence the scheduler happened to
    produce that run.

    That is a whole FAMILY of intermittent failures in this suite, not one
    test. Two members were caught in 2026-09-02's full-suite runs:
    `test_a_late_turn_finished_cannot_close_the_turn_that_replaced_it`, where
    acting early built the exact ordering the test exists to rule out, and
    `test_down_moves_the_highlight_not_the_cursor`, where the completion menu
    had not yet chosen a row. Both pass alone every time, which is what makes
    them expensive to diagnose from a CI log.

    So the rule for anything driving a deferred path: wait on the observable
    EFFECT, and wait for the whole burst rather than its first event.

    Raises on timeout rather than giving up quietly, because an effect that
    never lands is its own failure — a silent give-up reports it as whatever
    the next assertion happens to be about, which sends the reader to the
    wrong subsystem.
    """
    for _ in range(limit):
        await pilot.pause()
        if until():
            return
    raise AssertionError(f"never settled: waiting for {what}")
