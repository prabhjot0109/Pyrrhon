"""Voice channel: Pipecat pipeline + the on/off controller channels toggle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from pyrrhon.config.settings import Settings
from pyrrhon.core.events import Event
from pyrrhon.core.session import Session

# VoiceUnavailableError lives in the factory, which imports no pipecat — so the
# controller (and thus the TUI) imports cleanly without the audio stack. The
# pipeline (and pipecat) is imported lazily in start(), on the first /voice on.
from pyrrhon.voice.factory import VoiceUnavailableError

__all__ = ["VoiceController", "VoiceUnavailableError"]


class VoiceController:
    """Owns the background task running the voice pipeline (/voice on|off)."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        on_event: Callable[[Event], None] | None = None,
        notify: Callable[[str], None] = print,
    ):
        self._session = session
        self._settings = settings
        self._on_event = on_event
        self._notify = notify
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def update_settings(self, settings: Settings) -> None:
        """Swap the settings a future /voice on will build the pipeline from.

        Takes effect on the next start() — the pipeline reads providers/keys at
        build time, so a live pipeline keeps its config until toggled off/on."""
        self._settings = settings

    def start(self) -> str:
        if self.running:
            return "Voice is already on."
        try:
            # Lazy import: pulling pipecat at TUI startup slows launch, requires
            # the audio extras just to run in text mode, and lets pipecat's
            # logger grab the terminal before Textual owns it. Defer to here.
            from pyrrhon.voice import pipeline as _pipeline
        except ImportError as exc:
            return (
                f"Voice dependencies missing ({exc}). "
                'Run: uv add "pipecat-ai[local,silero,groq]" — staying in text mode.'
            )
        # _pipeline.run_voice (module attribute) so tests can monkeypatch it.
        self._task = asyncio.create_task(
            _pipeline.run_voice(
                self._session, self._settings, on_event=self._on_event
            )
        )
        self._task.add_done_callback(self._on_done)
        return "Voice: on. Talk normally — barge in whenever you like."

    async def stop(self) -> str:
        task = self._task
        if task is None or task.done():
            return "Voice is not running."
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, VoiceUnavailableError):
            pass
        return "Voice: off. Back to text."

    def _on_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if isinstance(exc, VoiceUnavailableError):
            self._notify(str(exc))
        elif exc is not None:
            self._notify(f"Voice stopped unexpectedly: {exc}. Text mode still works.")
