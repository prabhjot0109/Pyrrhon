import shutil
from pathlib import Path

from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.plugins import PluginManager
from pyrrhon.repl import build_agent, resolve_repo_code_consent
from tests.helpers import FakeLLM

FIXTURE_PLUGIN = Path(__file__).parent / "fixtures" / "plugins" / "hello-reviewer"


def repo_with_plugin(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / ".pyrrhon" / "plugins").mkdir(parents=True)
    (home / ".pyrrhon").mkdir(parents=True)
    shutil.copytree(FIXTURE_PLUGIN, repo / ".pyrrhon" / "plugins" / "hello-reviewer")
    return repo, home


def test_repo_plugin_code_is_gated_through_build_agent(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    agent = build_agent(repo, llm=FakeLLM([]), home=home)  # default: untrusted
    assert "checklist" not in agent.tools
    assert "Review style" in agent.system_prompt  # prompts load regardless of trust
    assert "# Plugin context" in agent.system_prompt


def test_repo_plugin_code_loads_with_allow_repo_code(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    agent = build_agent(repo, llm=FakeLLM([]), home=home, allow_repo_code=True)
    assert "checklist" in agent.tools


def test_global_plugin_code_loads_without_consent(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    (home / ".pyrrhon" / "plugins").mkdir(parents=True)
    shutil.copytree(FIXTURE_PLUGIN, home / ".pyrrhon" / "plugins" / "hello-reviewer")
    agent = build_agent(repo, llm=FakeLLM([]), home=home)
    assert "checklist" in agent.tools


async def test_checklist_tool_end_to_end_with_fakellm(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    fake = FakeLLM(
        [
            LLMReply(tool_calls=(ToolCall(id="c1", name="checklist", arguments={}),)),
            LLMReply(text="Start with correctness, then tests."),
        ]
    )
    agent = build_agent(repo, llm=fake, home=home, allow_repo_code=True)
    events = [event async for event in agent.run_turn([], "review my change")]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert finished and "Correctness" in finished[0].result_preview
    assert SpeechChunk(text="Start with correctness, then tests.") in events


def test_consent_helper_asks_once_and_records(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    manager = PluginManager(repo, home=home)
    questions: list[str] = []

    def say_yes(question: str) -> bool:
        questions.append(question)
        return True

    assert resolve_repo_code_consent(repo, manager, say_yes) is True
    assert questions and "hello-reviewer" in questions[0]
    trusted = (repo / ".pyrrhon" / "trusted").read_text(encoding="utf-8")
    assert trusted == "hello-reviewer\n"

    def explode(question: str) -> bool:
        raise AssertionError("consent must not be requested twice for the same repo")

    assert resolve_repo_code_consent(repo, manager, explode) is True


def test_consent_helper_declined_leaves_no_trust(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    manager = PluginManager(repo, home=home)
    assert resolve_repo_code_consent(repo, manager, lambda q: False) is False
    assert not (repo / ".pyrrhon" / "trusted").exists()


def test_consent_helper_never_asks_without_repo_code_plugins(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = PluginManager(repo, home=tmp_path / "home")

    def explode(question: str) -> bool:
        raise AssertionError("nothing to trust — must not ask")

    assert resolve_repo_code_consent(repo, manager, explode) is False
