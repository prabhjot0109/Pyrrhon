"""The input. A TextArea, because a question can be more than one line.

Textual's Input is single-line by definition, which made pasting a stack
trace or a multi-line snippet into the prompt impossible. TextArea takes
both, at the cost of owning the enter key explicitly.
"""

from __future__ import annotations

from textual.binding import Binding
from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea


class Prompt(TextArea):
    """Enter submits; shift+enter (or ctrl+j) inserts a newline.

    ctrl+j is not a second opinion about the keymap. Most terminals cannot
    distinguish shift+enter from enter at all — it needs the Kitty keyboard
    protocol — so without a plain-ASCII alternative the newline binding would
    be unreachable in Windows Terminal and every VS Code terminal, which is
    where this program actually runs.
    """

    BINDINGS = [
        Binding("enter", "submit", "send", priority=True),
        Binding("shift+enter", "newline", "newline", priority=True),
        Binding("ctrl+j", "newline", "newline", priority=True, show=False),
    ]

    class Submitted(Message):
        """Posted when the user sends the prompt."""

        def __init__(self, prompt: "Prompt", value: str) -> None:
            super().__init__()
            self.prompt = prompt
            self.value = value

        @property
        def control(self) -> "Prompt":
            return self.prompt

    # Set by the App once both widgets exist. None keeps this widget usable
    # on its own, which is what its tests rely on.
    completion = None

    def _menu_open(self) -> bool:
        return self.completion is not None and self.completion.display

    async def _on_key(self, event: Key) -> None:
        """Steer the completion menu before TextArea sees the key.

        up/down are TextArea's own cursor bindings, so the menu has to claim
        them here rather than through a binding, or the cursor moves and the
        highlight does not. escape is deliberately absent: the App binds it
        with priority and owns the whole precedence chain.
        """
        if not self._menu_open():
            return
        if event.key in ("up", "down"):
            self.completion.move(-1 if event.key == "up" else 1)
        elif event.key == "tab":
            self.accept_completion()
        else:
            return
        event.prevent_default()
        event.stop()

    def accept_completion(self) -> None:
        """Replace the typed prefix with the highlighted command."""
        name = self.completion.selected if self.completion else None
        if name is None:
            return
        self.completion.hide()
        self.text = f"/{name} "
        self.move_cursor(self.document.end)

    def action_submit(self) -> None:
        """enter completes a partial command, and runs a complete one.

        Sending "/mod" and being told it is unknown, with the answer on screen
        at the time, is the worst of both — so a partial name completes. But
        typing "/help" in full and pressing enter means run it: completing
        what is already complete would demand a second enter for no reason.
        """
        if self._menu_open():
            selected = self.completion.selected
            if selected is not None and self.text.strip() != f"/{selected}":
                self.accept_completion()
                return
            self.completion.hide()
        self.post_message(self.Submitted(self, self.text))

    def action_newline(self) -> None:
        self.insert(chr(10))

    @property
    def value(self) -> str:
        """Input-compatible accessor, so call sites read the same either way."""
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.text = text
