"""Shared channel scaffolding: one event-dispatch table for every renderer.

Both screen channels used to carry their own `isinstance` ladder over the
event stream, and the two had already drifted — the TUI rendered
ScreenArtifact and the REPL silently dropped it. Nothing failed, because a
missing `elif` is indistinguishable from a deliberate no-op.

So the mapping from event type to hook lives here, once, and is checked
against the `Event` union by a test. Adding an event to `pyrrhon.core.events`
without giving it a hook is now a test failure rather than an event that
quietly vanishes on some channels.

Renderers subclass `EventRenderer` and override only what they show. The
defaults are no-ops, which keeps "this channel deliberately ignores that" a
one-line omission — the thing that is no longer possible is *accidentally*
ignoring it.
"""

from __future__ import annotations

import logging

from pyrrhon.core.events import (
    AskUser,
    Citation,
    ScreenArtifact,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
    Transcription,
    TruncateSpeech,
    VoiceNotice,
)

log = logging.getLogger("pyrrhon.channels")

# Every member of the Event union maps to exactly one hook. Pinned by
# tests/test_event_dispatch.py against the union itself.
EVENT_HOOKS: dict[type, str] = {
    SpeechChunk: "on_speech",
    ScreenArtifact: "on_artifact",
    Citation: "on_citation",
    ToolCallStarted: "on_tool_started",
    ToolCallFinished: "on_tool_finished",
    AskUser: "on_question",
    Transcription: "on_transcription",
    VoiceNotice: "on_voice_notice",
    TruncateSpeech: "on_interrupted",
}


class EventRenderer:
    """Dispatches one core event to the matching `on_*` hook.

    Defaults no-op so a channel implements only what it displays: the plain
    REPL has no code pane and nothing to say about ToolCallFinished, while the
    TUI renders almost everything.
    """

    def render(self, event) -> None:
        hook = EVENT_HOOKS.get(type(event))
        if hook is None:
            # Reachable only for an event type added to the union without a
            # hook — which the dispatch-table test is there to prevent. Logged
            # rather than raised: a channel dropping an unknown event is
            # survivable, a channel crashing mid-turn is not.
            log.debug("no hook for event type %s", type(event).__name__)
            return
        getattr(self, hook)(event)

    def on_speech(self, event: SpeechChunk) -> None: ...
    def on_artifact(self, event: ScreenArtifact) -> None: ...
    def on_citation(self, event: Citation) -> None: ...
    def on_tool_started(self, event: ToolCallStarted) -> None: ...
    def on_tool_finished(self, event: ToolCallFinished) -> None: ...
    def on_question(self, event: AskUser) -> None: ...
    def on_transcription(self, event: Transcription) -> None: ...
    def on_voice_notice(self, event: VoiceNotice) -> None: ...
    def on_interrupted(self, event: TruncateSpeech) -> None: ...
