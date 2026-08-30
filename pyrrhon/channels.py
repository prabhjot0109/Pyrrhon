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
from inspect import isawaitable

from pyrrhon.core.events import (
    AskUser,
    Citation,
    ProviderRetrying,
    ScreenArtifact,
    SpeechChunk,
    ToolCallFinished,
    ToolCallStarted,
    Transcription,
    TruncateSpeech,
    TurnFinished,
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
    ProviderRetrying: "on_provider_retrying",
    TruncateSpeech: "on_interrupted",
    TurnFinished: "on_turn_finished",
}


class EventRenderer:
    """Dispatches one core event to the matching `on_*` hook.

    Defaults no-op so a channel implements only what it displays: the plain
    REPL has no code pane and nothing to say about ToolCallFinished, while the
    TUI renders almost everything.
    """

    def render(self, event) -> None:
        result = self._dispatch(event)
        if isawaitable(result):
            # An async hook reached through the synchronous door. Awaiting it
            # is the caller's job and this caller cannot, so the work would
            # simply never run — said out loud rather than left to surface as
            # a stray "coroutine was never awaited" warning three screens
            # later. Closed so the interpreter does not warn twice.
            result.close()
            log.error(
                "%s is async; that caller must use render_awaited()",
                EVENT_HOOKS[type(event)],
            )

    async def render_awaited(self, event) -> None:
        """`render`, for a caller that can wait for the hook to finish.

        A hook may be `async def` when what it shows needs the screen's own
        lifecycle to move first — the TUI seals one turn and opens the next
        when a spoken utterance arrives, and both halves await. Dispatched
        through the same table, so a hook is still written once and named
        once; the only difference is who is permitted to await it.

        This is also what keeps voice events in arrival order. A sync hook
        that deferred its own async work would schedule a second callback,
        which lands *behind* the events that arrived after it — so the answer
        to turn two was sealed into turn one's row. Awaiting here means one
        deferral per event instead of two.
        """
        result = self._dispatch(event)
        if isawaitable(result):
            await result

    def _dispatch(self, event):
        """Look up the hook and call it. Returns whatever it returned, which
        is `None` for a sync hook and a coroutine for an async one."""
        hook = EVENT_HOOKS.get(type(event))
        if hook is None:
            # Reachable only for an event type added to the union without a
            # hook — which the dispatch-table test is there to prevent. Logged
            # rather than raised: a channel dropping an unknown event is
            # survivable, a channel crashing mid-turn is not.
            log.debug("no hook for event type %s", type(event).__name__)
            return None
        return getattr(self, hook)(event)

    def on_speech(self, event: SpeechChunk) -> None: ...
    def on_artifact(self, event: ScreenArtifact) -> None: ...
    def on_citation(self, event: Citation) -> None: ...
    def on_tool_started(self, event: ToolCallStarted) -> None: ...
    def on_tool_finished(self, event: ToolCallFinished) -> None: ...
    def on_question(self, event: AskUser) -> None: ...
    def on_transcription(self, event: Transcription) -> None: ...
    def on_voice_notice(self, event: VoiceNotice) -> None: ...
    def on_provider_retrying(self, event: ProviderRetrying) -> None: ...
    def on_interrupted(self, event: TruncateSpeech) -> None: ...
    def on_turn_finished(self, event: TurnFinished) -> None: ...
