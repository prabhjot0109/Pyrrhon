from pathlib import Path

from textual.containers import VerticalScroll
from textual.widgets import Static

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.events import Citation
from pyrrhon.tui.app import PyrrhonApp
from pyrrhon.tui.prompt import Prompt
from pyrrhon.tui.status import StatusBar
from tests.helpers import FakeLLM


def make_app(repo: Path) -> PyrrhonApp:
    # A disposable copy, never tests/fixtures/sample_repo itself: mounting the
    # TUI warms the symbol index, which writes <repo>/.pyrrhon/cache.db.
    agent = build_agent(repo, llm=FakeLLM([]), home=repo.parent)
    return PyrrhonApp(repo_root=repo, agent=agent)


async def test_layout_is_one_full_width_column(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        transcript = app.query_one("#transcript", VerticalScroll)
        assert transcript is not None
        # D1: the transcript owns the full width, with nothing beside it.
        # outer_size, not size: the content region is 118 because of the
        # one-column padding, and padding is not another pane.
        assert transcript.outer_size.width == 120
        assert app.query_one("#prompt", Prompt).has_focus
        assert "understand" in app.query_one(StatusBar).status_text



async def test_ctrl_o_opens_the_last_citation(sample_repo: Path):
    app = make_app(sample_repo)
    seen: list[tuple[Path, Citation]] = []
    app._open_editor = lambda root, cit: seen.append((root, cit)) or None
    async with app.run_test(size=(120, 40)) as pilot:
        app.record_citation(Citation(file="utils/helpers.py", line=12))
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert len(seen) == 1
        assert seen[0][1] == Citation(file="utils/helpers.py", line=12)


async def test_ctrl_o_without_a_citation_does_not_kill_the_app(sample_repo: Path):
    app = make_app(sample_repo)
    called = []
    app._open_editor = lambda root, cit: called.append(cit) or None
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert called == []                       # nothing to open, nothing run
        assert app.query_one("#prompt", Prompt) is not None   # still alive


async def test_ctrl_o_surfaces_the_editors_complaint(sample_repo: Path):
    """A failure to open is a notification, never a dead app."""
    app = make_app(sample_repo)
    app._open_editor = lambda root, cit: "ERROR: could not run vim: nope"
    async with app.run_test(size=(120, 40)) as pilot:
        app.record_citation(Citation(file="utils/helpers.py", line=1))
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.query_one("#prompt", Prompt) is not None


# -- Phase 1: chrome -------------------------------------------------------


async def test_every_binding_advertises_itself(sample_repo: Path):
    """Footer renders the description, so a blank one is a key nobody sees."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        assert PyrrhonApp.BINDINGS, "the app declares its own keymap"
        for binding in PyrrhonApp.BINDINGS:
            assert binding.description, f"{binding.key} has no description"


async def test_the_theme_is_registered_and_selected(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)):
        assert app.theme == "pyrrhon"
        # $ink resolving at all is the proof the theme was in place before the
        # stylesheet parsed; an unregistered theme is a startup crash.
        assert app.get_theme("pyrrhon") is not None


def _transcript_text(app) -> str:
    """Everything on the transcript, as one string.

    Walks the mounted rows rather than a line buffer: since D3 the
    transcript is a widget tree, and that is the whole point of it.
    """
    from pyrrhon.tui.messages import Row

    return chr(10).join(
        str(getattr(row.body(), "content", ""))
        for row in app.query(Row)
    )


async def test_ctrl_l_clears_the_screen_not_the_session(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        from pyrrhon.tui.messages import NoticeRow
        app.query_one("#transcript", VerticalScroll).mount(NoticeRow("something to wipe"))
        await pilot.pause()
        assert "something to wipe" in _transcript_text(app)
        await pilot.press("ctrl+l")
        await pilot.pause()
        # Not "the transcript is empty": the background orientation task may
        # legitimately write again a tick later. What ctrl+l promises is that
        # what was there is gone.
        assert "something to wipe" not in _transcript_text(app)
        assert app.history == []          # nothing to lose here, but explicit
        assert app.query_one("#prompt", Prompt) is not None   # session survives


async def test_the_splash_fits_a_narrow_terminal(sample_repo: Path):
    """Defect 11: the 60-column block art wrapped mid-glyph below 62 columns."""
    app = make_app(sample_repo)
    async with app.run_test(size=(50, 20)) as pilot:
        await pilot.pause()
        rendered = app.query_one("#splash", Static).content
        for line in str(rendered).splitlines():
            assert len(line) <= 50, f"splash line overflows 50 columns: {line!r}"


async def test_the_splash_shows_the_block_art_when_it_fits(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "█" in str(app.query_one("#splash", Static).content)


async def test_a_repo_name_with_brackets_is_not_rich_markup(tmp_path: Path):
    """Defect 12: `weird[repo]` used to be parsed as markup on the way in."""
    repo = tmp_path / "weird[repo]"
    repo.mkdir()
    agent = build_agent(repo, llm=FakeLLM([]), home=tmp_path)
    app = PyrrhonApp(repo_root=repo, agent=agent)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "weird[repo]" in str(app.query_one("#splash", Static).content)


async def test_the_first_turn_takes_the_screen_back(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query("#splash")
        app.query_one("#prompt", Prompt).value = "/help"
        await pilot.press("enter")
        await pilot.pause()
        assert not app.query("#splash")


# -- Phase 4: the multiline prompt -----------------------------------------


async def test_a_two_line_paste_submits_as_one_message(sample_repo: Path):
    """Textual's Input is single-line by definition; a pasted snippet was
    impossible to send."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Prompt)
        prompt.text = "first line\nsecond line"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = _transcript_text(app)
        assert "first line" in text and "second line" in text
        assert prompt.text == "", "the prompt clears on submit"


async def test_shift_enter_inserts_a_newline_instead_of_submitting(sample_repo: Path):
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Prompt)
        prompt.focus()
        await pilot.press("a")
        await pilot.press("ctrl+j")     # the reachable alias for shift+enter
        await pilot.press("b")
        await pilot.pause()
        assert prompt.text == "a\nb"
        assert app.history == [], "nothing was submitted"


async def test_a_pasted_key_is_still_masked_in_the_echo(sample_repo: Path):
    """The transcript persists, so the credential must never reach it."""
    app = make_app(sample_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one("#prompt", Prompt)
        prompt.text = "/settings key GROQ_API_KEY super-secret-value"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = _transcript_text(app)
        assert "super-secret-value" not in text
        assert "****" in text
