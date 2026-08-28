"""The event contract between the headless core and every channel (REPL, TUI, voice, GUI).

Channels subscribe to this stream and render it however they like; the core
never knows who is listening.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechChunk:
    """Speakable prose — streamed to TTS in M3, printed in text channels."""

    text: str


@dataclass(frozen=True)
class ScreenArtifact:
    """Screen-only content (code, path lists, diagrams) — never spoken."""

    kind: str  # "code" | "paths" | "markdown"
    content: str


@dataclass(frozen=True)
class Citation:
    """A source location backing a claim. `file` is repo-relative, POSIX style."""

    file: str
    line: int | None = None
    snippet: str | None = None


@dataclass(frozen=True)
class ToolCallStarted:
    name: str
    args: dict


@dataclass(frozen=True)
class ToolCallFinished:
    name: str
    result_preview: str


@dataclass(frozen=True)
class AskUser:
    """Pyrrhon asking the user a (Socratic) question."""

    question: str


@dataclass(frozen=True)
class Transcription:
    """What STT heard the user say — the recognized text of a spoken turn.

    Screen-only: it has already been said aloud, so it is never re-spoken.
    Showing it closes the voice feedback loop — the user can see they were
    heard (and where they were *mis*heard). Without it, a working STT is
    indistinguishable from a dead mic.
    """

    text: str


@dataclass(frozen=True)
class VoiceNotice:
    """An out-of-band message from the voice pipeline for the screen: a
    provider error or status the user must see.

    Voice failures are otherwise silent — Pipecat marks provider errors
    non-fatal and only writes them to ~/.pyrrhon/logs/voice.log, so a TTS
    that 400s looks identical to a pipeline that never ran. This surfaces it.
    """

    text: str
    is_error: bool = False


@dataclass(frozen=True)
class TruncateSpeech:
    """The one reverse-direction event (channel → core).

    Emitted by the voice layer on barge-in. `played_text` is the prose the
    user actually heard before interrupting — word-level playback timestamps
    where the TTS service provides them, a duration-based estimate otherwise.
    The session rewrites the last assistant message to exactly this text:
    history never assumes knowledge of unspoken words.
    """

    played_text: str


@dataclass(frozen=True)
class TurnFinished:
    """The turn is over, however it ended.

    Emitted by whoever drove the turn, on every exit path including
    cancellation. A screen channel uses it to stop the spinner and to stop
    claiming the agent is still speaking; a channel that shows neither
    ignores it.

    It carries no payload. "Which turn" is the consumer's bookkeeping, not the
    core's: the TUI numbers its turns and remembers which one the utterance
    opened, so a report that arrives after something else has taken the screen
    closes nothing.
    """


Event = (
    SpeechChunk
    | ScreenArtifact
    | Citation
    | ToolCallStarted
    | ToolCallFinished
    | AskUser
    | Transcription
    | VoiceNotice
    | TruncateSpeech
    | TurnFinished
)
