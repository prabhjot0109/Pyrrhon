"""The six colours, in the one place they are allowed to be written (D6).

The rail carries an *epistemic ladder*, and that is what picks the hues. Green
means verified against a real line, amber means the claim was downgraded, red
means it faulted. Green-amber-red is the one colour sequence every reader
already knows how to rank, so the rail is legible before anything is explained
— which the old blue/teal pair could never be, because blue and teal have no
agreed order between them.

That frees the product's accent to mean one thing only: *you*. The wordmark,
the rail on a turn you took, the microphone and the focus ring are all `FACE`,
imported from branding.py rather than repeated.

The ground is true black. It was a blue-shifted near-black chosen to sit in
the same family as a blue accent, and with the accent warm that tint had
nothing left to agree with; a terminal agent should sit on the terminal's own
darkness rather than paint a coloured panel over it.

Every other module under pyrrhon/tui/ references these through `$tokens` in
pyrrhon.tcss. A hex value anywhere else is a bug a grep catches.
"""

from __future__ import annotations

from textual.theme import Theme

from pyrrhon.branding import FACE

INK = "#0b0b0d"        # background: black, with just enough lift off #000 that
                       # a true-black terminal still shows the app's edges
PANEL = "#17171a"      # one step up, for the status strip and the prompt
PAPER = "#e6e6e9"      # prose

VOICE = FACE           # you: your turns, the microphone, the focus ring
EVIDENCE = "#7ec699"   # verified against a line we actually opened
HEDGE = "#d9a441"      # downgraded claims, warnings
FAULT = "#e0625c"      # errors
MUTED = "#71717a"      # tool machinery, footer, durations

# The rail's vocabulary, addressable from the stylesheet by the same names the
# spec's glyph table uses.
#
# Deliberately NOT including the background. Textual builds $variables from the
# *active* theme only, so a token defined here and used in the stylesheet must
# be meaningful under any theme the user switches to — and a near-black
# background is not. The ink role is spelled $background/$surface in the
# stylesheet, which every theme defines, so switching themes restyles the app
# instead of crashing it with "reference to undefined variable".
TOKENS: dict[str, str] = {
    "evidence": EVIDENCE,
    "voice": VOICE,
    "hedge": HEDGE,
    "fault": FAULT,
    "muted": MUTED,
}

PYRRHON_THEME = Theme(
    name="pyrrhon",
    # primary is what Textual reaches for on its own chrome (the palette's
    # highlight, a Collapsible's arrow). That is the product speaking, so it
    # takes the accent rather than the evidence green.
    primary=VOICE,
    secondary=EVIDENCE,
    accent=VOICE,
    warning=HEDGE,
    error=FAULT,
    success=EVIDENCE,
    background=INK,
    surface=INK,
    panel=PANEL,
    foreground=PAPER,
    dark=True,
    variables=dict(TOKENS),
)
