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
