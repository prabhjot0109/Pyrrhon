from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.evidence import EvidenceLedger
from pyrrhon.core.grounding.gate import (
    HEDGE,
    LINE_HEDGE,
    LINE_UNSEEN_HEDGE,
    GroundedText,
    GroundingGate,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


async def test_verified_citation_survives_as_a_citation_not_as_speech():
    gate = GroundingGate(FIXTURE)
    out = await gate.check("greet lives at utils/helpers.py:1.")
    assert isinstance(out, GroundedText)
    # M15a: the reference verified, so it moves to the screen and leaves speech.
    assert out.speech_text == "greet lives at ."
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ()


async def test_missing_file_is_stripped_and_hedged():
    gate = GroundingGate(FIXTURE)
    out = await gate.check("see made/up/file.py:12 for details.")
    assert "made/up/file.py" not in out.speech_text
    assert out.speech_text == "see for details. I couldn't verify that location."
    assert out.citations == ()
    assert out.unverified == ("made/up/file.py:12",)


async def test_line_past_end_of_file_keeps_the_path_and_drops_the_line():
    """M10 policy change, signed off: the file verified, only the line did not.
    Deleting the path would throw away information the user can act on, and
    the broad hedge would overstate doubt about a file we just confirmed
    exists. Nothing unverified survives — the path passed the same existence
    and containment checks as any citation."""
    # utils/helpers.py has only 2 lines — the file is real, the line is not.
    out = await GroundingGate(FIXTURE).check("see utils/helpers.py:999.")
    assert out.citations == ()                              # still not a citation
    assert out.unverified == ("utils/helpers.py:999",)      # still reported as failed
    assert out.speech_text == "see utils/helpers.py. I couldn't confirm the exact line."


async def test_one_missing_file_forces_the_broader_hedge():
    """Mixing a real-file/bad-line reference with a wholly invented one must
    not let the narrower hedge understate the problem."""
    out = await GroundingGate(FIXTURE).check(
        "see utils/helpers.py:999 and made/up.py:1."
    )
    assert "made/up.py" not in out.speech_text
    assert "utils/helpers.py" in out.speech_text
    assert out.speech_text.endswith("I couldn't verify that location.")


async def test_mixed_refs_keep_verified_and_hedge_once():
    text = "greet is at utils/helpers.py:1; also bogus.py:3 and fake/x.py:9."
    out = await GroundingGate(FIXTURE).check(text)
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ("bogus.py:3", "fake/x.py:9")
    # M15a: verified goes to `citations`, not to speech — but one hedge still
    # covers the two that failed, which is the invariant this test guards.
    assert "utils/helpers.py" not in out.speech_text
    assert out.speech_text.count("I couldn't verify that location.") == 1


async def test_backslash_reference_verifies_as_posix():
    out = await GroundingGate(FIXTURE).check(r"look at utils\helpers.py:1")
    assert out.citations == (Citation(file="utils/helpers.py", line=1),)
    assert out.unverified == ()


async def test_unverified_backslash_form_is_stripped_from_speech():
    out = await GroundingGate(FIXTURE).check(r"see fake\thing.py:2 here")
    assert out.unverified == ("fake/thing.py:2",)
    assert out.speech_text == "see here I couldn't verify that location."


# -- the line-count cache (M10 Stage 2.4) -----------------------------------
#
# check() runs once per spoken sentence on the voice path, and once per
# markdown block on the text path, over the same two or three files. Before
# caching, each of those re-resolved and re-read every cited file.


async def test_line_count_cache_invalidates_when_the_file_grows(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\n", encoding="utf-8")
    gate = GroundingGate(tmp_path)

    assert (await gate.check("see a.py:2")).unverified == ()
    assert (await gate.check("see a.py:5")).unverified == ("a.py:5",)

    target.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    assert (await gate.check("see a.py:5")).unverified == ()


async def test_line_count_cache_invalidates_on_same_size_rewrite(tmp_path: Path):
    """mtime alone is not enough on a coarse-granularity filesystem, and size
    alone misses an equal-length edit — the key is the pair."""
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    gate = GroundingGate(tmp_path)
    assert (await gate.check("see a.py:3")).unverified == ()

    # Same byte count, fewer lines: 13 chars either way.
    target.write_text("one two three\n", encoding="utf-8")
    import os
    stamp = target.stat().st_mtime + 10
    os.utime(target, (stamp, stamp))
    assert (await gate.check("see a.py:3")).unverified == ("a.py:3",)


async def test_a_deleted_file_stops_verifying(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("one\n", encoding="utf-8")
    gate = GroundingGate(tmp_path)
    assert (await gate.check("see a.py:1")).unverified == ()

    target.unlink()
    assert (await gate.check("see a.py:1")).unverified == ("a.py:1",)


async def test_escaping_paths_are_cached_as_rejections(tmp_path: Path):
    """A hallucinated or escaping path is cited as often as a real one, so the
    negative result is worth caching too — but it must stay a rejection."""
    gate = GroundingGate(tmp_path)
    for _ in range(3):
        assert (await gate.check("see ../outside.py:1")).unverified != ()


# -- provenance: did we actually OPEN the line? (M13) ------------------------
#
# Three-way now, not two: verified-and-observed stands, verified-but-unobserved
# is downgraded to the bare path, unverified is stripped exactly as before.


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "\n".join(f"line {n}" for n in range(1, 51)), encoding="utf-8"
    )
    return tmp_path


async def test_an_observed_line_is_cited_normally(tmp_path: Path):
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    ledger = EvidenceLedger()
    ledger.record_range("app.py", 1, 40)
    result = await gate.check("The handler is at app.py:12.", ledger)
    # M15a: observed => citation, and citations are screen-only.
    assert result.speech_text == "The handler is at ."
    assert [(c.file, c.line) for c in result.citations] == [("app.py", 12)]
    assert result.unseen == ()


async def test_a_real_but_unobserved_line_is_downgraded_to_the_bare_path(tmp_path: Path):
    """The failure this milestone exists for: line 12 of app.py is real, so the
    old gate passed it — even though the model never opened the file."""
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    result = await gate.check("The handler is at app.py:12.", EvidenceLedger())
    assert "app.py:12" not in result.speech_text
    assert "app.py" in result.speech_text
    assert LINE_UNSEEN_HEDGE in result.speech_text
    assert result.unseen == ("app.py:12",)
    assert result.citations == ()


async def test_a_nonexistent_file_is_still_stripped_whole(tmp_path: Path):
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    result = await gate.check("See services/auth.py:9.", EvidenceLedger())
    assert "services/auth.py" not in result.speech_text
    assert result.unverified == ("services/auth.py:9",)


async def test_a_fabricated_path_outranks_an_unopened_line_in_the_hedge(tmp_path: Path):
    """Severity ordering: saying only "I haven't opened that line" when a wholly
    invented path is also present would understate the worse claim."""
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    result = await gate.check(
        "See app.py:12 and services/auth.py:9.", EvidenceLedger()
    )
    assert result.unseen == ("app.py:12",)
    assert result.unverified == ("services/auth.py:9",)
    assert result.speech_text.endswith(HEDGE)


async def test_an_out_of_range_line_outranks_an_unopened_one(tmp_path: Path):
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    result = await gate.check("See app.py:12 and app.py:999.", EvidenceLedger())
    assert result.unseen == ("app.py:12",)
    assert result.unverified == ("app.py:999",)
    assert result.speech_text.endswith(LINE_HEDGE)


async def test_provenance_off_preserves_todays_behaviour_exactly(tmp_path: Path):
    gate = GroundingGate(_repo(tmp_path), require_provenance=False)
    result = await gate.check("The handler is at app.py:12.", EvidenceLedger())
    assert result.speech_text == "The handler is at ."
    assert [(c.file, c.line) for c in result.citations] == [("app.py", 12)]
    assert result.unseen == ()


async def test_no_ledger_at_all_behaves_as_if_provenance_were_off(tmp_path: Path):
    """Callers that predate the ledger (tests, the M0 path) must not start
    hedging just because they pass nothing."""
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    result = await gate.check("The handler is at app.py:12.")
    assert result.speech_text == "The handler is at ."
    assert [(c.file, c.line) for c in result.citations] == [("app.py", 12)]


async def test_one_hedge_even_when_several_lines_are_unopened(tmp_path: Path):
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    result = await gate.check("See app.py:12, app.py:30 and app.py:44.", EvidenceLedger())
    assert result.unseen == ("app.py:12", "app.py:30", "app.py:44")
    assert result.speech_text.count(LINE_UNSEEN_HEDGE) == 1


async def test_a_partially_observed_file_downgrades_only_the_unseen_line(tmp_path: Path):
    """The model read lines 1-20; citing 12 is honest, citing 44 is not."""
    gate = GroundingGate(_repo(tmp_path), require_provenance=True)
    ledger = EvidenceLedger()
    ledger.record_range("app.py", 1, 20)
    result = await gate.check("See app.py:12 and app.py:44.", ledger)
    assert [(c.file, c.line) for c in result.citations] == [("app.py", 12)]
    assert result.unseen == ("app.py:44",)
    # M15a: 12 became a citation (screen-only); 44 was downgraded to the bare
    # path plus a hedge. Neither coordinate is spoken.
    assert "app.py:12" not in result.speech_text
    assert "app.py:44" not in result.speech_text


async def test_verified_citations_are_stripped_from_speech(tmp_path):
    """Verify is not verbalize: the screen shows path:line, the voice never says it.

    Spoken coordinates are unusable — a listener cannot act on "app dot py
    colon twelve" — so a VERIFIED reference leaves speech_text and survives in
    citations. Nothing about the gate itself relaxes: this reference passed
    every existence and containment check.
    """
    (tmp_path / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 51)))
    gate = GroundingGate(tmp_path)

    result = await gate.check("The retry lives in app.py:12 and it backs off.")

    assert "app.py:12" not in result.speech_text
    assert "app.py" not in result.speech_text
    assert result.citations == (Citation(file="app.py", line=12),)
    # A citation merely moved to the screen must NOT trigger the hedge.
    assert "I couldn't verify" not in result.speech_text
    assert result.speech_text == "The retry lives in and it backs off."


async def test_counters_sort_every_reference_into_exactly_one_arm(tmp_path):
    """M16e's criterion has to be readable before anything is changed.

    The gate already sorts three ways; until now it kept no tally, so "the
    intervention rate fell" was unfalsifiable. Each arm is counted from what
    survived into the text: a real line promotes, a real file with a bad line
    keeps its path, an invented file keeps nothing.
    """
    (tmp_path / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 51)))
    gate = GroundingGate(tmp_path)

    await gate.check("nothing to cite here.")
    await gate.check("the retry is at app.py:12.")
    await gate.check("see app.py:999 and made/up.py:3.")

    assert gate.counters.checks == 3
    assert gate.counters.promoted == 1
    assert gate.counters.hedged == 1
    assert gate.counters.stripped == 1
    # One check of three intervened. The third carried TWO failures and still
    # counts once: the rate is per sentence, not per claim, which is why the
    # raw arms are reported beside it.
    assert gate.counters.intervened == 1
    assert gate.counters.intervention_rate == 1 / 3


async def test_unopened_line_counts_as_a_hedge_not_a_strip(tmp_path):
    """The provenance arm is a hedge by construction: the path IS verified,
    only the "we looked at it" claim is not. Counting it as a strip would make
    require_provenance look like it fabricates paths."""
    (tmp_path / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 51)))
    gate = GroundingGate(tmp_path, require_provenance=True)

    out = await gate.check("the retry is at app.py:12.", EvidenceLedger())

    assert out.unseen == ("app.py:12",)
    assert (gate.counters.hedged, gate.counters.stripped) == (1, 0)
    assert gate.counters.promoted == 0


def test_counters_sum_across_gates():
    """The eval builds one agent per case, so a run-level number only exists
    if per-case counters add."""
    from pyrrhon.core.grounding.gate import GateCounters

    total = GateCounters(checks=2, intervened=1, promoted=3) + GateCounters(
        checks=1, hedged=1, stripped=2
    )
    assert total == GateCounters(
        checks=3, intervened=1, promoted=3, hedged=1, stripped=2
    )
