from pathlib import Path

from pyrrhon.bootstrap import build_agent
from pyrrhon.commands import builtin  # noqa: F401 — registers /init, /model, /code
from pyrrhon.commands.registry import CommandContext, dispatch
from pyrrhon.core.events import Citation
from tests.helpers import FakeLLM

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class StubUI:
    def __init__(self):
        self.notes: list[str] = []
        self.last_citation: Citation | None = None

    def notify(self, text: str) -> None:
        self.notes.append(text)


def make_ctx(repo_root: Path = FIXTURE) -> CommandContext:
    agent = build_agent(repo_root, llm=FakeLLM([]))
    return CommandContext(repo_root=repo_root, agent=agent, ui=StubUI())


async def test_init_scaffolds_soul_via_dispatch(tmp_path: Path):
    response = await dispatch("/init", make_ctx(tmp_path))
    assert "soul file created" in response
    assert (tmp_path / ".pyrrhon" / "soul.md").is_file()


async def test_model_fast_swaps_agent_llm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ctx = make_ctx()
    old_llm = ctx.agent.llm
    response = await dispatch("/model fast openai/gpt-4.1-mini", ctx)
    assert response == "fast slot is now openai/gpt-4.1-mini."
    assert ctx.agent.llm is not old_llm
    assert ctx.agent.llm.model == "gpt-4.1-mini"


async def test_model_deep_stores_slot_for_m4(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    ctx = make_ctx()
    # provider is the first path segment; the model may itself contain slashes
    await dispatch("/model deep openrouter/deepseek/deepseek-r1", ctx)
    assert ctx.agent.deep_llm.model == "deepseek/deepseek-r1"


async def test_model_bad_usage_and_unknown_provider():
    ctx = make_ctx()
    assert (await dispatch("/model fast", ctx)).startswith("ERROR: usage:")
    assert (await dispatch("/model warp openai/gpt-4.1-mini", ctx)).startswith("ERROR: usage:")
    assert (await dispatch("/model fast doesnotexist/m", ctx)).startswith("ERROR:")
    assert (await dispatch("/model fast openai/", ctx)).startswith("ERROR: usage:")


async def test_model_missing_key_is_error(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    ctx = make_ctx()
    response = await dispatch("/model fast cerebras/llama3.3-70b", ctx)
    assert response.startswith("ERROR:")
    assert "CEREBRAS_API_KEY" in response


async def test_code_without_citation_is_error():
    assert (await dispatch("/code", make_ctx())).startswith("ERROR: no citation")


async def test_code_launches_vscode(monkeypatch):
    launched: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd):
            launched.append(cmd)

    monkeypatch.setattr("pyrrhon.commands.builtin.which", lambda _: "C:/fake/bin/code")
    monkeypatch.setattr("pyrrhon.commands.builtin.Popen", FakePopen)
    ctx = make_ctx()
    ctx.ui.last_citation = Citation(file="utils/helpers.py", line=1)
    response = await dispatch("/code", ctx)
    assert response == "Opened utils/helpers.py:1 in VS Code."
    assert launched[0][0] == "C:/fake/bin/code"
    assert launched[0][1] == "--goto"
    assert launched[0][2].endswith("helpers.py:1")


async def test_code_missing_cli_is_error(monkeypatch):
    monkeypatch.setattr("pyrrhon.commands.builtin.which", lambda _: None)
    ctx = make_ctx()
    ctx.ui.last_citation = Citation(file="app.py", line=1)
    assert (await dispatch("/code", ctx)).startswith("ERROR: VS Code CLI")
