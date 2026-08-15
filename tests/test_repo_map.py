"""Ranked repo map: most-referenced symbols per file, token-budgeted."""

from pyrrhon.core.tools.ast_index import RepoMapTool, SymbolIndex


def _make_repo(tmp_path):
    (tmp_path / "core.py").write_text(
        "def hot():\n    return 1\n\ndef cold():\n    return 2\n", encoding="utf-8"
    )
    for i in range(3):  # three files call hot(); nothing calls cold()
        (tmp_path / f"user{i}.py").write_text(
            "from core import hot\n\ndef go():\n    return hot()\n", encoding="utf-8"
        )
    return tmp_path


async def test_repo_map_ranks_hot_symbols_first(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    repo_map = await index.build_repo_map()
    assert "core.py" in repo_map
    assert repo_map.index("hot") < repo_map.index("cold")   # within core.py
    assert repo_map.splitlines()[0].startswith("core.py")   # hottest file first


async def test_repo_map_respects_char_budget(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    small = await index.build_repo_map(max_chars=80)
    assert len(small) <= 80 + 40  # budget plus one truncation notice line


async def test_repo_map_tool_runs_end_to_end(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    out = await RepoMapTool(index).run()
    assert "core.py" in out
    assert ":" in out  # symbols carry line numbers for citation


# -- repo-map memoisation (M10 Stage 2.4) -----------------------------------


async def test_repo_map_is_memoised_until_the_index_changes(tmp_path):
    """The query runs a correlated subquery per symbol row and then rebuilds
    the whole string; nothing about the result can change while the index
    does not."""
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()

    first = await index.build_repo_map()
    generation = index._generation
    assert await index.build_repo_map() == first
    assert index._generation == generation  # no reindex, no rebuild

    (tmp_path / "newcomer.py").write_text(
        "from core import hot\n\ndef also():\n    return hot()\n", encoding="utf-8"
    )
    await index.ensure_fresh(force=True)
    assert index._generation > generation
    assert "newcomer.py" in await index.build_repo_map()


async def test_repo_map_cache_is_keyed_on_the_budget(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    big = await index.build_repo_map(max_chars=6000)
    small = await index.build_repo_map(max_chars=40)
    assert small != big
    assert "truncated" in small


async def test_mentioned_files_are_boosted_to_the_top(tmp_path):
    # `hot.py` is referenced more; `quiet.py` is what the user is asking about.
    (tmp_path / "hot.py").write_text("def popular():\n    pass\n", encoding="utf-8")
    (tmp_path / "quiet.py").write_text("def obscure():\n    pass\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from hot import popular\n\npopular()\npopular()\npopular()\n", encoding="utf-8"
    )
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()

    plain = await index.build_repo_map()
    boosted = await index.build_repo_map(mentioned=frozenset({"quiet.py"}))

    assert plain.index("hot.py") < plain.index("quiet.py")
    assert boosted.index("quiet.py") < boosted.index("hot.py")


async def test_the_boost_does_not_invent_files(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    rendered = await index.build_repo_map(mentioned=frozenset({"ghost.py"}))
    assert "ghost.py" not in rendered


async def test_the_cache_distinguishes_different_mention_sets(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    first = await index.build_repo_map(mentioned=frozenset({"a.py"}))
    second = await index.build_repo_map(mentioned=frozenset({"b.py"}))
    assert first != second  # a generation-only cache key would return `first`


async def test_the_tool_asks_the_agent_what_the_conversation_is_about(tmp_path):
    """RepoMapTool takes a callable, not a back-reference to the Agent: the
    tool must not know what an Agent is."""
    (tmp_path / "hot.py").write_text("def popular():\n    pass\n", encoding="utf-8")
    (tmp_path / "quiet.py").write_text("def obscure():\n    pass\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from hot import popular\n\npopular()\npopular()\n", encoding="utf-8"
    )
    index = SymbolIndex(tmp_path)
    tool = RepoMapTool(index, mentions=lambda: frozenset({"quiet.py"}))
    rendered = await tool.run()
    assert rendered.index("quiet.py") < rendered.index("hot.py")


def test_conversation_mentions_come_from_the_recent_turns_only(tmp_path):
    """A long session must not end up with every file 'mentioned' — that is
    the same as no personalisation, but with a bigger cache key."""
    from pyrrhon.core.agent.loop import Agent
    from tests.helpers import FakeLLM

    agent = Agent(llm=FakeLLM([]), tools=[], system_prompt="", repo_root=tmp_path)
    history = [{"role": "user", "content": f"look at old{i}.py:1"} for i in range(20)]
    history.append({"role": "assistant", "content": "it is in pyrrhon/core/loop.py:42"})
    found = agent._conversation_mentions(history)
    assert "pyrrhon/core/loop.py" in found
    assert "old0.py" not in found  # scrolled out of the window
    assert "old19.py" in found


def test_build_agent_actually_connects_the_two(tmp_path, monkeypatch):
    """The wiring, not the pieces: build_agent has to patch the callable AFTER
    constructing the Agent, and a silent failure here looks exactly like a map
    that simply never personalises."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from pyrrhon.core.tools.ast_index import RepoMapTool
    from pyrrhon.repl import build_agent
    from tests.helpers import FakeLLM

    agent = build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)
    agent._mentions_now = frozenset({"pyrrhon/core/agent/loop.py"})

    belts = [agent.tools["repo_map"], *(
        t for t in agent.tools["think_deeper"].tools.values() if isinstance(t, RepoMapTool)
    )]
    assert len(belts) == 2  # main belt and the deep subagent's
    for tool in belts:
        assert tool._mentions() == frozenset({"pyrrhon/core/agent/loop.py"})
