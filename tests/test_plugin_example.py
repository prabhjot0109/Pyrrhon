import shutil
from pathlib import Path

from pyrrhon.core.events import SpeechChunk, ToolCallFinished
from pyrrhon.core.providers.llm import LLMReply, ToolCall
from pyrrhon.repl import build_agent, load_channel_plugins
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


# M11 folded resolve_repo_code_consent into the single startup gate: plugin
# code is now one line of the same prompt that covers repo config and soul
# files. These tests keep their original questions, asked of the new door.


def test_consent_is_requested_once_and_recorded(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    questions: list[str] = []

    def say_yes(question: str) -> bool:
        questions.append(question)
        return True

    plugins, _settings = load_channel_plugins(repo, say_yes, home=home)
    assert questions and "hello-reviewer" in questions[0]
    assert any(p.tools for p in plugins), "code ran, so the consent took effect"
    assert (repo / ".pyrrhon" / "trusted").read_text(encoding="utf-8") == "hello-reviewer\n"

    def explode(_question: str) -> bool:
        raise AssertionError("consent must not be requested twice for the same repo")

    plugins2, _s2 = load_channel_plugins(repo, explode, home=home)
    assert any(p.tools for p in plugins2)


def test_declining_leaves_no_trust_and_runs_no_code(tmp_path: Path):
    repo, home = repo_with_plugin(tmp_path)
    plugins, _settings = load_channel_plugins(repo, lambda _q: False, home=home)
    assert not (repo / ".pyrrhon" / "trusted").exists()
    assert not any(p.tools for p in plugins)
    assert plugins, "prompts still load; only executable contributions are gated"


def test_nothing_dangerous_means_no_question(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def explode(_question: str) -> bool:
        raise AssertionError("nothing to trust — must not ask")

    load_channel_plugins(repo, explode, home=tmp_path / "home")


def test_a_second_untrusted_plugin_re_gates_the_first(tmp_path: Path):
    """load_all takes one flag for every repo plugin, so consent is
    all-or-nothing. A repo that adds a plugin after the first was approved must
    be asked again — otherwise the new one's code runs unseen."""
    repo, home = repo_with_plugin(tmp_path)
    load_channel_plugins(repo, lambda _q: True, home=home)
    second = repo / ".pyrrhon" / "plugins" / "late-arrival"
    shutil.copytree(FIXTURE_PLUGIN, second)
    (second / "plugin.toml").write_text(
        (second / "plugin.toml").read_text(encoding="utf-8").replace(
            'name = "hello-reviewer"', 'name = "late-arrival"'
        ),
        encoding="utf-8",
    )
    plugins, _settings = load_channel_plugins(repo, lambda _q: False, home=home)
    assert not any(p.tools for p in plugins), "refusing must re-gate both plugins"
