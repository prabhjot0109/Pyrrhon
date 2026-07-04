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
