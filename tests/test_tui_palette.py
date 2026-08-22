"""Phase 4: the command palette provider (D4).

No second copy of the command list, and no new dependency: the provider reads
the registry at search time, so a command registered after startup is
findable without anything being told about it.
"""

from pathlib import Path

from pyrrhon.bootstrap import build_agent
from pyrrhon.commands.registry import CommandContext, all_commands, command
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.palette import PyrrhonCommands
from tests.helpers import FakeLLM


def make_app(repo: Path) -> PyrrhonApp:
    agent = build_agent(repo, llm=FakeLLM([]), home=repo.parent)
    return PyrrhonApp(repo_root=repo, agent=agent)


async def test_the_provider_is_composed_with_textuals_own():
    """Composed, not replacing: Textual's own palette entries survive."""
    from textual.app import App

    assert PyrrhonCommands in PyrrhonApp.COMMANDS
    assert App.COMMANDS <= PyrrhonApp.COMMANDS


async def test_discovery_yields_one_hit_per_registered_command(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        provider = PyrrhonCommands(app.screen)
        hits = [hit async for hit in provider.discover()]
        assert len(hits) == len(all_commands())
        assert "/help" in {hit.display for hit in hits}
        # help_text becomes the hit description, so the palette explains itself.
        assert all(hit.help for hit in hits)


async def test_a_command_registered_after_startup_appears(sample_repo: Path):
    """A plugin (M7) registers into the same table at runtime."""
    from pyrrhon.commands.registry import _COMMANDS

    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        provider = PyrrhonCommands(app.screen)
        before = len([hit async for hit in provider.discover()])

        @command("only-in-this-test", "A command registered after startup")
        def _late(args: str, ctx: CommandContext) -> str:
            return "late"

        try:
            after = [hit async for hit in provider.discover()]
            assert len(after) == before + 1
            assert "/only-in-this-test" in {hit.display for hit in after}
        finally:
            # The registry is process-global by design, so a test that adds to
            # it has to take it back out; otherwise /help grows a phantom entry
            # for every test that runs after this one.
            _COMMANDS.pop("only-in-this-test", None)


async def test_search_matches_and_runs_through_dispatch(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        provider = PyrrhonCommands(app.screen)
        hits = [hit async for hit in provider.search("help")]
        # By name, not by rank. The registry is process-global, so a plugin
        # test that ran earlier leaves /hello registered, and fuzzy-matching
        # "help" scores it above /help. Asserting on hits[0] made this test a
        # hostage to suite order.
        chosen = [hit for hit in hits if "/help" in str(hit.match_display)]
        assert chosen, f"searching 'help' finds /help, got {hits}"
        await chosen[0].command()
        # run_command is the app's one execution path, and it records the
        # response — so this proves the palette did not grow a second one.
        assert "/model" in app.last_command_response
