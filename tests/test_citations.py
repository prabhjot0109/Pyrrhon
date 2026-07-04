from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_citations, extract_references

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def test_extracts_existing_file_citation():
    text = "Greeting lives at utils/helpers.py:1 and is called from app.py:5."
    cites = extract_citations(text, FIXTURE)
    assert Citation(file="utils/helpers.py", line=1) in cites
    assert Citation(file="app.py", line=5) in cites


def test_skips_paths_that_do_not_exist():
    cites = extract_citations("see made/up/file.py:12", FIXTURE)
    assert cites == []


def test_dedupes_and_normalizes_backslashes():
    text = r"utils\helpers.py:1 and again utils/helpers.py:1"
    cites = extract_citations(text, FIXTURE)
    assert cites == [Citation(file="utils/helpers.py", line=1)]


def test_skips_paths_that_escape_root():
    cites = extract_citations("see a/../../../../pyproject.toml:1", FIXTURE)
    assert cites == []


def test_extract_references_keeps_nonexistent_paths():
    refs = extract_references("see made/up/file.py:12 and app.py:5")
    assert refs == [("made/up/file.py", 12), ("app.py", 5)]


def test_extract_references_normalizes_backslashes_and_keeps_duplicates():
    refs = extract_references(r"utils\helpers.py:1 twice: utils/helpers.py:1")
    assert refs == [("utils/helpers.py", 1), ("utils/helpers.py", 1)]
