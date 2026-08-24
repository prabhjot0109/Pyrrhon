"""The status bar: what Pyrrhon is running on, and how full it is (D7).

Reactive rather than rebuilt (defect 14). Every field is a Textual reactive,
so a latency measurement arriving does not repaint the model names, and the
voice state can be polled ten times a second without costing anything when it
has not changed.

The context meter is the interesting one. Token accounting has existed since
M15b — `history_tokens` against `Agent.context_budget_tokens`, corrected by
the calibrated `token_scale` — and no channel has ever read it (defect 8).
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from pyrrhon.tui.theme import MUTED, VOICE

# Names, not hex. The rule is that a widget module never writes a colour, and
# importing the token satisfies it in the way the theme intends.
VOICE_OFF = "off"


class StatusBar(Static):
    """One line: repo, mode, models, context fill, last latency, voice state.

    The repo name arrived here when the Header was deleted. It is state, not
    identity, and a value shown in two places is a value you have to check
    twice.
    """

    repo: reactive[str] = reactive("")
    mode: reactive[str] = reactive("understand")
    fast_model: reactive[str] = reactive("")
    deep_model: reactive[str] = reactive("")
    latency_ms: reactive[float | None] = reactive(None)
    # None means "no budget configured", which renders as nothing at all
    # rather than as 0% — an unknown is not an empty context window.
    context_pct: reactive[int | None] = reactive(None)
    voice_state: reactive[str] = reactive(VOICE_OFF)

    def render(self) -> Text:
        parts = [self.mode, self.fast_model or "unknown"]
        if self.deep_model:
            parts.append(f"deep {self.deep_model}")
        if self.context_pct is not None:
            parts.append(f"ctx {self.context_pct}%")
        if self.latency_ms is not None:
            # Spec "live latency": last turn's user text -> first SpeechChunk.
            parts.append(f"{self.latency_ms:.0f} ms")
        line = Text()
        if self.repo:
            # The one field that is not a setting, so it leads and it is the
            # only one that is not muted.
            line.append(self.repo, style=VOICE)
            line.append("  ", style=MUTED)
        line.append(" · ".join(parts), style=MUTED)
        if self.voice_state != VOICE_OFF:
            line.append(" · ", style=MUTED)
            line.append(f"🎙 {self.voice_state}", style=VOICE)
        return line

    @property
    def status_text(self) -> str:
        """The rendered line as plain text. For tests and for /debug."""
        return self.render().plain


def sync(
    bar: StatusBar,
    *,
    repo: str,
    mode: str,
    fast_model: str,
    deep_model: str,
    latency_ms: float | None,
    context_used: int,
    context_budget: int,
    voice_state: str,
) -> None:
    """Push one snapshot into the bar.

    Here rather than in the App because deciding what the instruments say and
    deciding how they read are one job. Each assignment is a reactive write,
    so only the fields that actually changed repaint (defect 14).
    """
    bar.repo = repo
    bar.mode = mode
    bar.fast_model = fast_model
    bar.deep_model = deep_model
    bar.latency_ms = latency_ms
    # None, not 0, when no budget is configured: an unknown is not an empty
    # context window, and 0% would be a claim nobody measured.
    bar.context_pct = round(100 * context_used / context_budget) if context_budget else None
    bar.voice_state = voice_state
