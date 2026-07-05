"""Playback-position tracking: how much of the answer did the user actually hear?

Spec: on barge-in, history is rewritten to the played text. Word-level
playback timestamps are used where the TTS service provides them (Pipecat
emits TTSTextFrame per word in playback order for e.g. Cartesia/ElevenLabs);
OpenAI TTS — the M3 default — provides none, so we fall back to a
duration-based character estimate cut at a word boundary.

Pure Python on purpose: no pipecat imports, fully unit-testable.
"""

from __future__ import annotations

import time
from collections.abc import Callable

# ~180 spoken words/min ≈ 3 words/sec ≈ 15 chars/sec including spaces.
# Deliberately conservative: underestimating what was heard only means the
# rewritten history admits to slightly less than the user heard — safe.
DEFAULT_CHARS_PER_SEC = 15.0


class PlaybackTracker:
    def __init__(
        self,
        chars_per_sec: float = DEFAULT_CHARS_PER_SEC,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._chars_per_sec = chars_per_sec
        self._clock = clock
        self._queued: list[str] = []
        self._played_words: list[str] = []
        self._started_at: float | None = None
        self._finished = False

    def reset(self) -> None:
        self._queued = []
        self._played_words = []
        self._started_at = None
        self._finished = False

    def speech_queued(self, text: str) -> None:
        """Record prose sent to TTS (one SpeechChunk)."""
        stripped = text.strip()
        if stripped:
            self._queued.append(stripped)

    def playback_started(self) -> None:
        """The bot's audio started coming out of the speakers."""
        if self._started_at is None:
            self._started_at = self._clock()
        self._finished = False

    def word_played(self, word: str) -> None:
        """A word-timestamped TTS service confirmed this word was played."""
        stripped = word.strip()
        if stripped:
            self._played_words.append(stripped)

    def playback_finished(self) -> None:
        """The bot finished speaking — everything queued was heard."""
        self._finished = True

    def played_text(self) -> str:
        """Best estimate of the prose the user has heard so far."""
        if self._played_words:
            return " ".join(self._played_words)
        full = " ".join(self._queued)
        if not full:
            return ""
        if self._finished:
            return full
        if self._started_at is None:
            return ""
        elapsed = max(self._clock() - self._started_at, 0.0)
        chars = int(elapsed * self._chars_per_sec)
        if chars >= len(full):
            return full
        boundary = full.rfind(" ", 0, chars + 1)
        if boundary <= 0:
            return full[:chars]
        return full[:boundary]
