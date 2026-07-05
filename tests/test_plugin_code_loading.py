import logging
from pathlib import Path

from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.plugins import PluginManager, read_trusted, record_trusted
from tests.helpers import write_plugin

TOOLS_PY = '''\
from pathlib import Path

from pyrrhon.core.tools.base import Tool

# Side effect on import: lets tests prove whether this module was executed.
Path(__file__).with_name("imported.flag").write_text("imported", encoding="utf-8")


class PingTool(Tool):
    name = "ping"
    description = "Reply with pong."
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> str:
        return "pong"


def get_tools():
    return [PingTool()]
'''

CODE_MANIFEST = '''\
name = "pinger"
version = "0.1.0"

[contributes]
prompts = ["*.md"]
tools = "tools.py:get_tools"
'''

COMMANDS_PY = '''\
from pyrrhon.commands.registry import command


@command("hello-plugin", "Say hello from the test plugin")
async def hello_plugin(args: str, ctx) -> str:
    return "hello from plugin"


def get_commands():
    return None  # registration already happened via the decorator at import time
'''


def make_dirs(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".pyrrhon" / "plugins").mkdir(parents=True)
    (repo / ".pyrrhon" / "plugins").mkdir(parents=True)
    return home, repo


def test_global_plugin_code_loads_without_consent(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    plugin_dir = write_plugin(
        home / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY, "style.md": "Ping politely."},
    )
    plugins = PluginManager(repo, home=home).load_all()  # allow_repo_code stays False
    assert [t.name for t in plugins[0].tools] == ["ping"]
    assert (plugin_dir / "imported.flag").is_file()


def test_repo_plugin_code_gated_without_consent(tmp_path: Path, caplog):
    home, repo = make_dirs(tmp_path)
    plugin_dir = write_plugin(
        repo / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY, "style.md": "Ping politely."},
    )
    with caplog.at_level(logging.WARNING, logger="pyrrhon.plugins"):
        plugins = PluginManager(repo, home=home).load_all(allow_repo_code=False)
    assert plugins[0].tools == []                          # code NOT executed
    assert not (plugin_dir / "imported.flag").exists()     # module never imported
    assert "Ping politely." in plugins[0].prompt_text      # prompts still load
    assert "not trusted" in caplog.text


def test_repo_plugin_code_loads_with_consent(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    plugin_dir = write_plugin(
        repo / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY, "style.md": "Ping politely."},
    )
    plugins = PluginManager(repo, home=home).load_all(allow_repo_code=True)
    assert [t.name for t in plugins[0].tools] == ["ping"]
    assert (plugin_dir / "imported.flag").is_file()


def test_import_error_skips_that_plugin_only(tmp_path: Path, caplog):
    home, repo = make_dirs(tmp_path)
    write_plugin(
        home / ".pyrrhon" / "plugins", "broken",
        CODE_MANIFEST.replace('"pinger"', '"broken"'),
        files={"tools.py": "raise RuntimeError('boom')\n"},
    )
    write_plugin(
        home / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY},
    )
    with caplog.at_level(logging.WARNING, logger="pyrrhon.plugins"):
        plugins = PluginManager(repo, home=home).load_all()
    assert [p.manifest.name for p in plugins] == ["pinger"]
    assert "failed to load code" in caplog.text


def test_entry_point_escaping_plugin_dir_is_rejected(tmp_path: Path, caplog):
    home, repo = make_dirs(tmp_path)
    (home / ".pyrrhon" / "evil.py").write_text(
        "def get_tools():\n    return []\n", encoding="utf-8"
    )
    write_plugin(
        home / ".pyrrhon" / "plugins", "escaper",
        'name = "escaper"\nversion = "0.1.0"\n\n'
        '[contributes]\ntools = "../../evil.py:get_tools"\n',
    )
    with caplog.at_level(logging.WARNING, logger="pyrrhon.plugins"):
        plugins = PluginManager(repo, home=home).load_all()
    assert plugins == []
    assert "failed to load code" in caplog.text


def test_trusted_file_roundtrip(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert read_trusted(repo) == set()
    record_trusted(repo, ["a", "b"])
    record_trusted(repo, ["b", "c"])  # no duplicates appended
    assert read_trusted(repo) == {"a", "b", "c"}
    content = (repo / ".pyrrhon" / "trusted").read_text(encoding="utf-8")
    assert content == "a\nb\nc\n"


def test_repo_code_plugins_lists_only_repo_level_executables(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    write_plugin(  # global + code: not the channel's problem
        home / ".pyrrhon" / "plugins", "pinger", CODE_MANIFEST,
        files={"tools.py": TOOLS_PY},
    )
    write_plugin(  # repo + prompts only: no consent needed
        repo / ".pyrrhon" / "plugins", "styler",
        'name = "styler"\nversion = "0.1.0"\n\n[contributes]\nprompts = ["*.md"]\n',
    )
    write_plugin(  # repo + commands entry point: consent needed
        repo / ".pyrrhon" / "plugins", "cmds",
        'name = "cmds"\nversion = "0.1.0"\n\n'
        '[contributes]\ncommands = "commands.py:get_commands"\n',
        files={"commands.py": COMMANDS_PY},
    )
    assert PluginManager(repo, home=home).repo_code_plugins() == ["cmds"]


async def test_commands_entry_point_registers_slash_command(tmp_path: Path):
    home, repo = make_dirs(tmp_path)
    write_plugin(
        home / ".pyrrhon" / "plugins", "greeter",
        'name = "greeter"\nversion = "0.1.0"\n\n'
        '[contributes]\ncommands = "commands.py:get_commands"\n',
        files={"commands.py": COMMANDS_PY},
    )
    PluginManager(repo, home=home).load_all()
    ctx = CommandContext(repo_root=repo, agent=None, ui=None)
    assert await dispatch("/hello-plugin", ctx) == "hello from plugin"
