"""The face of the product: one banner, rendered by every channel.

The mascot is a small skeptical owl (Pyrrho of Elis was the original
skeptic; the owl asks for citations). ASCII-only and <= 60 columns so it
renders identically in cmd.exe, the TUI transcript pane, and over SSH.
"""

from __future__ import annotations

from pyrrhon import __version__

_OWL = r"""
   ___
  (o,o)   P Y R R H O N  v{version}
  {{`"'}}   a skeptical engineer for your codebase
   -"-    every claim cited, or it isn't said
"""


def banner() -> str:
    return _OWL.format(version=__version__)
