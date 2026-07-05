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
    launched: list[str] = []
    monkeypatch.setattr("pyrrhon.tui.app.run_tui", lambda repo: launched.append(repo))
    main(["some/repo"])
    assert launched == ["some/repo"]


def test_text_flag_launches_repl(monkeypatch):
    launched: list[str] = []
    monkeypatch.setattr("pyrrhon.repl.run_repl", lambda repo: launched.append(repo))
    main(["--text", "some/repo"])
    assert launched == ["some/repo"]
