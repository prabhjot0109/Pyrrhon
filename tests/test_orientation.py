"""The orientation brief: the first thing worth knowing about an unfamiliar repo."""

from pyrrhon.core.events import ScreenArtifact
from pyrrhon.core.tools.ast_index import SymbolIndex
from pyrrhon.core.tools.orientation import build_orientation


async def test_the_brief_is_a_screen_artifact_not_speech(tmp_path):
    """Screen-only by construction: it is a dense list of paths and counts,
    which is precisely what VOICE_STYLE forbids reading aloud."""
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    brief = await build_orientation(tmp_path, index)
    assert isinstance(brief, ScreenArtifact)
    assert brief.kind == "markdown"


async def test_the_brief_names_the_languages_and_the_busiest_files(tmp_path):
    (tmp_path / "core.py").write_text("def shared():\n    pass\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from core import shared\n\nshared()\nshared()\n", encoding="utf-8"
    )
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    content = (await build_orientation(tmp_path, index)).content
    assert "python" in content.lower()
    assert "core.py" in content


async def test_a_polyglot_repo_is_reported_as_polyglot(polyglot_repo):
    """The census is the payoff of Task 2 — a brief that said 'python' about a
    Go repo would be worse than no brief."""
    index = SymbolIndex(polyglot_repo)
    await index.ensure_fresh()
    content = (await build_orientation(polyglot_repo, index)).content.lower()
    assert "typescript" in content
    assert "go" in content


async def test_an_empty_repo_produces_an_honest_brief(tmp_path):
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    content = (await build_orientation(tmp_path, index)).content
    assert "no indexed source" in content.lower()


async def test_a_repo_with_no_git_history_still_gets_a_brief(tmp_path):
    """tmp_path is not a git repo. GitLogTool returns an error string rather
    than raising, but the brief must not present that as history."""
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    content = (await build_orientation(tmp_path, index)).content
    assert "a.py" in content
    assert "ERROR" not in content


async def test_the_channel_helper_renders_the_brief_without_blocking(tmp_path, monkeypatch):
    """orient_in_background is fire-and-forget: the brief must arrive via the
    render callback, and a failure inside it must never escape to startup."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from pyrrhon.repl import build_agent, orient_in_background
    from tests.helpers import FakeLLM

    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)

    rendered = []
    task = orient_in_background(agent, rendered.append)
    await task

    assert len(rendered) == 1
    assert isinstance(rendered[0], ScreenArtifact)
    assert "a.py" in rendered[0].content


async def test_a_failing_brief_never_breaks_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from pyrrhon.repl import build_agent, orient_in_background
    from tests.helpers import FakeLLM

    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)

    def _explode(_brief):
        raise RuntimeError("renderer is broken")

    await orient_in_background(agent, _explode)  # must not raise
