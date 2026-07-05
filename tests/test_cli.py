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
        "pyrrhon.tui.app.run_tui", lambda repo, voice=False: launched.append((repo, voice))
    )
    main(["some/repo"])
    assert launched == [("some/repo", False)]


def test_text_flag_launches_repl(monkeypatch):
    launched: list[tuple] = []
    monkeypatch.setattr(
        "pyrrhon.repl.run_repl", lambda repo, voice=False: launched.append((repo, voice))
    )
    main(["--text", "some/repo"])
    assert launched == [("some/repo", False)]


def test_voice_flag_reaches_the_tui(monkeypatch):
    launched: list[tuple] = []
    monkeypatch.setattr(
        "pyrrhon.tui.app.run_tui", lambda repo, voice=False: launched.append((repo, voice))
    )
    main(["--voice", "some/repo"])
    assert launched == [("some/repo", True)]
