"""M16e's prompt changes, pinned by direction rather than by wording.

The prompt is prose and will be reworded; what must not drift is which way it
points. M16b set this precedent for the land-nudge, which reads the opposite
way from the reference's and would be silently "fixed" by anyone who copied
the reference back in. The same hazard applies here twice: an admissibility
rule with no counterweight produces a model that hedges everything, and a
tool policy that says "search first" without saying why reads as a style note.
"""

from pyrrhon.core.agent.prompts import SYSTEM_PROMPT


def test_a_location_is_admissible_only_from_this_turn():
    """Recollection is not a source. The file may have changed since, and a
    stale in-range line passes every check the gate makes — which is why the
    rule has to live in the prompt and cannot be enforced downstream."""
    assert "ADMISSIBLE" in SYSTEM_PROMPT
    assert "THIS turn" in SYSTEM_PROMPT
    # An earlier turn's findings still answer the question; only the
    # coordinate has to be re-derived.
    assert "may not cite a coordinate from it" in SYSTEM_PROMPT


def test_the_rule_ships_with_its_counterweight():
    """The central risk of the milestone: a model told never to state an
    unread location can retreat into hedging everything, which reads as
    honesty and is a loss. The rule bounds citations, not confidence."""
    assert "bounds your CITATIONS, not your confidence" in SYSTEM_PROMPT
    assert "Hedging everything is not honesty" in SYSTEM_PROMPT


def test_the_tool_policy_names_all_five_cases():
    """Each line is a real failure an earlier milestone paid for: the
    transcript's 400-line read, its argument-bearing repo_map, M16c's pager
    (where re-running is the most expensive possible recovery), and M16d's
    firewall."""
    for phrase in (
        "Search before you read",
        "Read the range the search pointed at",
        "goes to explore",
        "PAGED, not re-run",
        "repo_map takes no arguments",
    ):
        assert phrase in SYSTEM_PROMPT, phrase
