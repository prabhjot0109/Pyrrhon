from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.gate import GroundedText, GroundingGate

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_verified_citation_passes_through_untouched():
    gate = GroundingGate(FIXTURE)
    out = await gate.check("greet lives at utils/helpers.py:1.")
    assert isinstance(out, GroundedText)
    assert out.speech_text == "greet lives at utils/helpers.py:1."
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ()


async def test_missing_file_is_stripped_and_hedged():
    gate = GroundingGate(FIXTURE)
    out = await gate.check("see made/up/file.py:12 for details.")
    assert "made/up/file.py" not in out.speech_text
    assert out.speech_text == "see for details. I couldn't verify that location."
    assert out.citations == ()
    assert out.unverified == ("made/up/file.py:12",)


async def test_line_past_end_of_file_fails_verification():
    # utils/helpers.py has only 2 lines — the file is real, the line is not.
    out = await GroundingGate(FIXTURE).check("see utils/helpers.py:999.")
    assert out.citations == ()
    assert out.unverified == ("utils/helpers.py:999",)
    assert out.speech_text.endswith("I couldn't verify that location.")


async def test_mixed_refs_keep_verified_and_hedge_once():
    text = "greet is at utils/helpers.py:1; also bogus.py:3 and fake/x.py:9."
    out = await GroundingGate(FIXTURE).check(text)
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ("bogus.py:3", "fake/x.py:9")
    assert "utils/helpers.py:1" in out.speech_text
    assert out.speech_text.count("I couldn't verify that location.") == 1


async def test_backslash_reference_verifies_as_posix():
    out = await GroundingGate(FIXTURE).check(r"look at utils\helpers.py:1")
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ()


async def test_unverified_backslash_form_is_stripped_from_speech():
    out = await GroundingGate(FIXTURE).check(r"see fake\thing.py:2 here")
    assert out.unverified == ("fake/thing.py:2",)
    assert out.speech_text == "see here I couldn't verify that location."
