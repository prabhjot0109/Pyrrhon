"""Keep pipecat's logs off Pyrrhon's terminal.

pipecat logs through loguru, whose default sink is a *direct* reference to
`sys.stderr` captured at import time. Pyrrhon imports pipecat before Textual
takes over the screen, so that sink holds the real terminal — every pipecat
log (starting with its import-time banner) would then write raw ANSI straight
through the TUI's managed screen and corrupt it.

Fix: before the first pipecat import, drop loguru's stderr sink and send its
output to a file instead. Called at the top of bridge.py (the module that
performs the first pipecat import). Voice failures stay diagnosable in the log
file rather than being scribbled over the UI.
"""

from __future__ import annotations

from pathlib import Path

_configured = False


def route_pipecat_logs_to_file() -> None:
    """Send loguru (pipecat's logger) to ~/.pyrrhon/logs/voice.log, not stderr.

    Idempotent and best-effort: if loguru is absent (voice deps not installed)
    there is nothing to silence, so it's a no-op.
    """
    global _configured
    if _configured:
        return
    try:
        from loguru import logger
    except ImportError:
        _configured = True
        return
    logdir = Path.home() / ".pyrrhon" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    logger.remove()  # drop the default stderr sink that would deface the TUI
    logger.add(
        logdir / "voice.log",
        level="INFO",
        rotation="1 MB",
        retention=3,
        enqueue=True,  # non-blocking: logging never stalls the audio loop
    )
    _configured = True
