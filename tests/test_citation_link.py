"""Clickable citations: the answer points at path:line instead of pasting code,
so the pointer has to actually open."""

from pyrrhon.core.citation_link import citation_markup, citation_uri
from pyrrhon.core.events import Citation


def test_a_citation_becomes_a_file_uri_with_a_line_anchor(tmp_path):
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    uri = citation_uri(tmp_path, Citation(file="mod.py", line=5))
    assert uri.startswith("file:///")
    assert uri.endswith("#L5")
    assert "mod.py" in uri


def test_a_citation_without_a_line_still_links_to_the_file(tmp_path):
    """The gate downgrades an unopened line to a bare path; that path should
    still be openable rather than becoming dead text."""
    uri = citation_uri(tmp_path, Citation(file="mod.py"))
    assert uri.startswith("file:///") and "#L" not in uri


def test_a_path_escaping_the_repo_is_not_linked(tmp_path):
    """The path came from model output. The gate bounds it, but this is the
    step that hands it to the user's shell, so it re-checks rather than trusts."""
    assert citation_uri(tmp_path, Citation(file="../../etc/passwd", line=1)) is None


def test_escaping_paths_still_render_as_plain_text(tmp_path):
    markup = citation_markup(tmp_path, Citation(file="../../etc/passwd", line=1))
    assert "link=" not in markup
    assert "etc/passwd" in markup  # shown, just not clickable


def test_markup_is_clickable_for_a_normal_citation(tmp_path):
    markup = citation_markup(tmp_path, Citation(file="pkg/mod.py", line=12))
    assert "[link=file:///" in markup
    assert "pkg/mod.py:12" in markup
