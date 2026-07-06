"""Regression tests for the two defects that garbled the TUI (2026-07-06).

1. Importing VoiceController must NOT import pipecat. pipecat's loguru sink
   grabs the terminal at import; pulling it in at TUI startup let its logs
   deface Textual's screen (and forced the audio extras just to run text mode).
2. route_pipecat_logs_to_file() must leave no stderr sink, so pipecat logs
   land in a file instead of being scribbled over the UI.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_voice_controller_does_not_import_pipecat():
    # Fresh interpreter: other tests may have already imported pipecat in-process.
    code = (
        "import sys; from pyrrhon.voice import VoiceController; "
        "sys.exit(1 if 'pipecat' in sys.modules else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, (
        "importing VoiceController pulled in pipecat — it must be lazy.\n"
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # And nothing was written to the terminal (no pipecat banner on stderr).
    assert result.stderr == "", f"unexpected terminal output: {result.stderr!r}"


def test_route_pipecat_logs_leaves_no_stderr_sink():
    from loguru import logger

    from pyrrhon.voice import _logging

    _logging._configured = False  # force a fresh configuration
    _logging.route_pipecat_logs_to_file()

    sinks = [getattr(h, "_name", "") for h in logger._core.handlers.values()]
    assert sinks, "expected at least one loguru sink after routing"
    assert "<stderr>" not in sinks, f"stderr sink still present: {sinks}"

    # Idempotent: a second call must not stack a duplicate file sink.
    before = len(logger._core.handlers)
    _logging.route_pipecat_logs_to_file()
    assert len(logger._core.handlers) == before
