"""The input. A TextArea, because a question can be more than one line.

Textual's Input is single-line by definition, which made pasting a stack
trace or a multi-line snippet into the prompt impossible. TextArea takes
both, at the cost of owning the enter key explicitly.
"""

from __future__ import annotations

from textual.binding import Binding
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

    def action_submit(self) -> None:
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
