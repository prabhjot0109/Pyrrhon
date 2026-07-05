from pathlib import Path

from pyrrhon.commands import plugins_cmd  # noqa: F401 — registers /plugins
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.tools.base import Tool
from pyrrhon.plugins import LoadedPlugin, PluginContributes, PluginManifest


class ChecklistStub(Tool):
    name = "checklist"
    description = "stub"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> str:
        return "ok"


def make_manifest() -> PluginManifest:
    return PluginManifest(
        name="hello-reviewer",
        version="0.1.0",
        contributes=PluginContributes(
            prompts=["prompts/*.md"], tools="tools.py:get_tools"
        ),
    )


async def test_plugins_command_reports_empty(tmp_path: Path):
    ctx = CommandContext(repo_root=tmp_path, agent=None, ui=None)
    assert await dispatch("/plugins", ctx) == "No plugins loaded."


async def test_plugins_command_lists_name_version_scope_and_contributions(tmp_path: Path):
    plugin = LoadedPlugin(
        manifest=make_manifest(),
        dir=tmp_path / ".pyrrhon" / "plugins" / "hello-reviewer",
        tools=[ChecklistStub()],
        prompt_text="# Review style",
    )
    ctx = CommandContext(repo_root=tmp_path, agent=None, ui=None, plugins=[plugin])
    out = await dispatch("/plugins", ctx)
    assert "hello-reviewer@0.1.0 [repo]" in out
    assert "prompts: prompts/*.md" in out
    assert "tools: checklist" in out


async def test_plugins_command_marks_untrusted_repo_tools(tmp_path: Path):
    plugin = LoadedPlugin(  # declared tools, but code was gated: tools list empty
        manifest=make_manifest(),
        dir=tmp_path / ".pyrrhon" / "plugins" / "hello-reviewer",
        tools=[],
        prompt_text="",
    )
    ctx = CommandContext(repo_root=tmp_path, agent=None, ui=None, plugins=[plugin])
    out = await dispatch("/plugins", ctx)
    assert "declared but not loaded" in out
