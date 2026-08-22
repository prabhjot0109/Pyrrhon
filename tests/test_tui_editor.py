"""ctrl+o's half of the citation contract (D2).

Covers what the retired CodeViewer tests used to: containment, and an
unreadable file reported rather than crashed. The guard itself is not
re-implemented here — open_in_editor asks citation_uri for the verdict.
"""

from pathlib import Path

import pytest

from pyrrhon.core.events import Citation
from pyrrhon.tui.editor import editor_argv, open_in_editor


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "helpers.py").write_text("def greet():\n    pass\n")
    return tmp_path


def test_vi_style_editor_gets_the_line_as_a_plus_argument(repo, monkeypatch):
    monkeypatch.setenv("EDITOR", "vim")
    seen: list[list[str]] = []
    assert open_in_editor(repo, Citation(file="utils/helpers.py", line=2),
                          run=lambda argv: seen.append(argv) or 0) is None
    assert seen[0][:2] == ["vim", "+2"]
    assert seen[0][2].endswith("helpers.py")


def test_code_style_editor_gets_goto(repo, monkeypatch):
    monkeypatch.setenv("EDITOR", "code")
    seen: list[list[str]] = []
    open_in_editor(repo, Citation(file="utils/helpers.py", line=7),
                   run=lambda argv: seen.append(argv) or 0)
    assert seen[0][1] == "--goto"
    assert seen[0][2].endswith("helpers.py:7")


def test_visual_wins_over_editor(repo, monkeypatch):
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.setenv("VISUAL", "nano")
    seen: list[list[str]] = []
    open_in_editor(repo, Citation(file="utils/helpers.py", line=1),
                   run=lambda argv: seen.append(argv) or 0)
    assert seen[0][0] == "nano"


def test_flags_in_the_editor_variable_survive(repo, monkeypatch):
    """$EDITOR is a command line, not a program name."""
    monkeypatch.setenv("EDITOR", "code -w")
    seen: list[list[str]] = []
    open_in_editor(repo, Citation(file="utils/helpers.py", line=3),
                   run=lambda argv: seen.append(argv) or 0)
    assert seen[0][:3] == ["code", "-w", "--goto"]


def test_an_unrecognised_editor_gets_the_bare_path(repo, monkeypatch):
    """A guessed flag would be read as a second filename."""
    monkeypatch.setenv("EDITOR", "my-weird-editor")
    seen: list[list[str]] = []
    open_in_editor(repo, Citation(file="utils/helpers.py", line=4),
                   run=lambda argv: seen.append(argv) or 0)
    assert len(seen[0]) == 2 and seen[0][0] == "my-weird-editor"


def test_a_path_escaping_the_repo_opens_nothing(repo, monkeypatch):
    monkeypatch.setenv("EDITOR", "vim")
    called = []
    msg = open_in_editor(repo, Citation(file="../../outside.py", line=1),
                         run=lambda argv: called.append(argv) or 0)
    assert called == []
    assert msg is not None and "escapes the repo" in msg


def test_a_missing_file_is_a_message_not_a_crash(repo, monkeypatch):
    monkeypatch.setenv("EDITOR", "vim")
    called = []
    msg = open_in_editor(repo, Citation(file="does/not/exist.py", line=3),
                         run=lambda argv: called.append(argv) or 0)
    assert called == []
    assert msg is not None and "could not read" in msg


def test_no_editor_configured_names_the_variable(repo, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    msg = open_in_editor(repo, Citation(file="utils/helpers.py", line=1))
    assert msg is not None and "$EDITOR" in msg


def test_a_missing_editor_binary_is_a_message(repo, monkeypatch):
    monkeypatch.setenv("EDITOR", "not-installed")

    def boom(argv):
        raise OSError("No such file or directory")

    msg = open_in_editor(repo, Citation(file="utils/helpers.py", line=1), run=boom)
    assert msg is not None and msg.startswith("ERROR: could not run")


def test_a_citation_without_a_line_passes_the_bare_path():
    target = Path("/tmp/x.py")
    assert editor_argv("vim", target, None) == ["vim", str(target)]
