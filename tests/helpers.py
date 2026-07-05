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
    """Duck-typed stand-in for OpenAICompatLLM: returns scripted replies in order."""

    def __init__(self, replies: list[LLMReply]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMReply:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self._replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self._replies.pop(0)
