import pytest

from pyrrhon import __version__
from pyrrhon.cli import main


def test_version_is_current():
    assert __version__ == "0.1.0"


def test_cli_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_default_launches_tui(monkeypatch):
    launched: list[tuple] = []
    monkeypatch.setattr(
        "pyrrhon.tui.app.run_tui", lambda repo, voice=False, trust_repo=False: launched.append((repo, voice, trust_repo))
    )
    main(["some/repo"])
    assert launched == [("some/repo", False, False)]


def test_text_flag_launches_repl(monkeypatch):
    launched: list[tuple] = []
    monkeypatch.setattr(
        "pyrrhon.repl.run_repl", lambda repo, voice=False, trust_repo=False: launched.append((repo, voice, trust_repo))
    )
    main(["--text", "some/repo"])
    assert launched == [("some/repo", False, False)]


def test_voice_flag_reaches_the_tui(monkeypatch):
    launched: list[tuple] = []
    monkeypatch.setattr(
        "pyrrhon.tui.app.run_tui", lambda repo, voice=False, trust_repo=False: launched.append((repo, voice, trust_repo))
    )
    main(["--voice", "some/repo"])
    assert launched == [("some/repo", True, False)]


def test_setup_flag_runs_the_wizard_then_launches(monkeypatch):
    calls = []
    monkeypatch.setattr("pyrrhon.config.wizard.run_wizard", lambda: calls.append("wizard") or "ok")
    monkeypatch.setattr("pyrrhon.tui.app.run_tui", lambda repo, voice=False, trust_repo=False: calls.append("tui"))
    from pyrrhon.cli import main

    main(["--setup"])
    assert calls == ["wizard", "tui"]


def test_trust_repo_flag_reaches_the_channel(monkeypatch):
    """--trust-repo grants a repo's servers, providers, and soul files without
    prompting. If it silently stopped reaching the channel, automation would
    look like it worked while every grant was refused."""
    launched: list[tuple] = []
    monkeypatch.setattr(
        "pyrrhon.tui.app.run_tui",
        lambda repo, voice=False, trust_repo=False: launched.append((repo, trust_repo)),
    )
    main(["--trust-repo", "some/repo"])
    assert launched == [("some/repo", True)]
