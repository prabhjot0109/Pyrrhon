"""The six colours, in the one place they are allowed to be written (D6).

Two semantic hues rather than one accent is the deliberate part. Blue means
*verified*, teal means *spoken*, and they map to the two distinctions a user
of this specific product needs to make at a glance. The blue is not chosen:
it is the wordmark's, imported from branding.py rather than repeated, and
finally applied to the chrome it was always meant to anchor.

Every other module under pyrrhon/tui/ references these through `$tokens` in
pyrrhon.tcss. A hex value anywhere else is a bug a grep catches.
"""

from __future__ import annotations

from textual.theme import Theme

from pyrrhon.branding import FACE

INK = "#0d0f14"        # background: near-black, shifted blue so the accent
                       # sits in the same family
EVIDENCE = FACE        # verified rail, citations, focus ring, primary
VOICE = "#4ec9d4"      # everything the microphone owns
HEDGE = "#d9a441"      # downgraded claims, warnings
FAULT = "#e05252"      # errors
MUTED = "#6b7280"      # tool machinery, footer, durations

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
    primary=EVIDENCE,
    secondary=VOICE,
    accent=EVIDENCE,
    warning=HEDGE,
    error=FAULT,
    success=VOICE,
    background=INK,
    surface=INK,
    panel="#161a23",   # one step off the background, for the status strip
    foreground="#d7dae0",
    dark=True,
    variables=dict(TOKENS),
)
