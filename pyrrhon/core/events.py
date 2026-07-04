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


Event = (
    SpeechChunk
    | ScreenArtifact
    | Citation
    | ToolCallStarted
    | ToolCallFinished
    | AskUser
)
