"""M16e's prompt changes, pinned by direction rather than by wording.

The prompt is prose and will be reworded; what must not drift is which way it
points. M16b set this precedent for the land-nudge, which reads the opposite
way from the reference's and would be silently "fixed" by anyone who copied
the reference back in. The same hazard applies here twice: an admissibility
rule with no counterweight produces a model that hedges everything, and a
tool policy that says "search first" without saying why reads as a style note.
"""

from pyrrhon.core.agent.prompts import SYSTEM_PROMPT, TEXT_STYLE


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


def flat(text: str) -> str:
    """The prompt is hard-wrapped prose, so a phrase that fits on one line
    today straddles two after the next reword. Every assertion below runs
    against the unwrapped text: what is pinned is the sentence, never where
    the wrap happens to fall."""
    return " ".join(text.split())


def test_the_belt_says_out_loud_that_it_cannot_write():
    """M18.3. Nothing on the belt edits a file, and until now nothing said so,
    so the model could offer a change it had no way to make. A capability the
    model has to infer from the absence of a tool is one it infers wrong under
    pressure to be helpful."""
    assert "NO EDITOR" in flat(SYSTEM_PROMPT)
    assert "leave the edit to them" in flat(SYSTEM_PROMPT)


def test_an_empty_search_is_a_named_case_rather_than_a_dead_end():
    """M18.5. The prompt said search before read and said nothing about what
    to do when the search comes back empty. These three failures have
    different right answers, and the wrong one — reading a missed guess as
    proof of absence — is the one that sounds confident."""
    for phrase in (
        "may be NAMED differently",
        "not silently retried",
        "An honest absence is a real answer",
    ):
        assert phrase in flat(SYSTEM_PROMPT), phrase


def test_one_exemplar_carries_the_answer_shape():
    """M18.4. DESIGN_PROMPT had a style exemplar and the base prompt described
    its answer shape in prose instead. Four lines of transcript land it where
    three paragraphs did not."""
    assert "The shape of a good answer" in flat(SYSTEM_PROMPT)
    # Named after the transcript, so the transcript reads as an instance of a
    # rule rather than as one nice answer.
    assert "Punchline, then the WHY, then what the trade-off costs, then ONE" in flat(
        SYSTEM_PROMPT
    )


def test_a_dispatch_table_sits_under_the_argued_tool_policy():
    """M18.8. The prose above it earns its length by arguing why; the table
    costs about sixty tokens and answers "which one" without re-reading the
    argument."""
    assert "Which tool, at a glance" in flat(SYSTEM_PROMPT)


def test_the_thread_belongs_to_both_channels():
    """M18.6. "Offer the next hop, one at a time" lived only in VOICE_STYLE,
    which made the product's personality voice-only by accident rather than by
    decision."""
    assert "offering the next thread, one at a time" in flat(TEXT_STYLE)
    assert "BOTH channels, not only aloud" in flat(TEXT_STYLE)


def test_text_mode_is_bounded():
    """M18.7. "You can be thorough" with no shape behind it invites a survey.
    The ceiling is soft on purpose — a hard word count would truncate the one
    question whose honest answer is long — so what is pinned is that a ceiling
    is stated and that the overflow becomes the next hop."""
    assert "answers most questions completely" in flat(TEXT_STYLE)
    assert "offer the rest as the next hop" in flat(TEXT_STYLE)
