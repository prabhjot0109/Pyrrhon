from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import extract_citations

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
