"""The status bar. The code viewer used to live here too; see D1.

A permanent half-screen source window was the wrong answer to "where is
that": it showed one location, cost 40% of the width on every turn
including the ones that cite nothing, and duplicated the editor the user
already has open. Its containment guard is not lost — the same check lives
in `citation_uri`, which `editor.open_in_editor` calls.
"""

from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """One-line status: mode plus the two model slots."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status_text: str = ""

    def show_status(
        self,
        mode: str,
        fast_model: str,
        deep_model: str,
        latency_ms: float | None = None,
    ) -> None:
        self.status_text = f"mode: {mode} · fast: {fast_model} · deep: {deep_model}"
        if latency_ms is not None:
            # Spec "live latency": last turn's user text -> first SpeechChunk.
            self.status_text += f" · {latency_ms:.0f} ms"
        self.update(self.status_text)
