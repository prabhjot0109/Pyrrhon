"""Open a citation in the user's own editor (D2).

The second of two independent routes to a cited line. The OSC 8 link on the
citation row is the mouse path and works wherever the terminal supports it;
this is the keyboard path, and it is the only mechanism that reliably lands
on a line number. Neither depends on the other, so a terminal without OSC 8
and a machine without $EDITOR each still have one working route.

No Textual import here on purpose. `App.suspend()` is the caller's job, so
this module is a pure function of the environment and unit-tests without a
running app.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from pyrrhon.core.citation_link import citation_uri
from pyrrhon.core.events import Citation

# Editors that take the line as a separate `+N` argument before the path.
_PLUS_LINE = frozenset(
    {"vi", "vim", "nvim", "gvim", "view", "nano", "pico", "emacs", "emacsclient",
     "joe", "micro", "kak", "helix", "hx", "ne", "mg"}
)
# VS Code and its forks, which need --goto to honour the line at all.
_GOTO = frozenset({"code", "code-insiders", "codium", "vscodium", "cursor", "windsurf"})


def _split_editor(value: str) -> list[str]:
    """$EDITOR may carry flags ("code -w"), so it is a command line, not a name.

    posix=False on Windows because posix mode eats the backslashes in
    `C:\Program Files\...`; the quote stripping afterwards is what posix mode
    would otherwise have done for us.
    """
    if os.name == "nt":
        return [tok.strip('"') for tok in shlex.split(value, posix=False) if tok.strip('"')]
    return shlex.split(value)


def editor_argv(editor: str, path: Path, line: int | None) -> list[str]:
    """The argv for `editor`, with the line passed the way it actually accepts.

    An unrecognised editor gets the bare path rather than a guessed flag: it
    opens the right file, which beats a flag it will treat as a filename.
    """
    argv = _split_editor(editor)
    if not argv:
        return []
    name = Path(argv[0]).name.lower().removesuffix(".exe")
    if line and name in _GOTO:
        return [*argv, "--goto", f"{path}:{line}"]
    if line and name in _PLUS_LINE:
        return [*argv, f"+{line}", str(path)]
    return [*argv, str(path)]


def open_in_editor(
    repo_root: Path,
    citation: Citation,
    run: Callable[[list[str]], int] = subprocess.call,
) -> str | None:
    """Open `citation` in $VISUAL/$EDITOR. None on success, else a message.

    Every failure is a sentence, never a traceback: this runs off a keypress
    in a live TUI, and a dead app is a worse answer than "set $EDITOR".
    """
    if citation_uri(repo_root, citation) is None:
        return f"ERROR: citation escapes the repo: {citation.file}"
    path = (repo_root / citation.file).resolve()
    if not path.is_file():
        return f"ERROR: could not read {citation.file}"

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        return "No editor configured — set $EDITOR (or $VISUAL) to open citations."

    argv = editor_argv(editor, path, citation.line)
    if not argv:
        return "No editor configured — set $EDITOR (or $VISUAL) to open citations."
    try:
        code = run(argv)
    except OSError as exc:
        return f"ERROR: could not run {argv[0]}: {exc}"
    return None if code == 0 else f"{argv[0]} exited with {code}"
