"""Slash-command completion, offered while you type.

The redesign spec ruled an inline `/` dropdown out and rejected
`textual-autocomplete` with it. The dependency is still rejected — this is
Textual's own OptionList over the same registry the palette reads — but the
affordance is not: a command table nobody can see is a command table nobody
uses, and ctrl+p is discovery for people who already know the palette exists.
"""

from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from pyrrhon.commands.registry import all_commands


def matches(text: str) -> list[tuple[str, str]]:
    """(name, help) for every command the prompt text could still become.

    Only while the text is a bare `/name` with no argument yet: once a space
    is typed the user is writing arguments, and a menu over that is noise.
    """
    if not text.startswith("/") or len(text.splitlines()) > 1:
        return []
    typed = text[1:]
    if " " in typed:
        return []
    return [
        (cmd.name, cmd.help_text)
        for cmd in all_commands()
        if cmd.name.startswith(typed)
    ]


class CommandMenu(OptionList):
    """The list itself. Hidden unless it has something to offer."""

    def show(self, found: list[tuple[str, str]]) -> None:
        self.clear_options()
        if not found:
            self.display = False
            return
        for name, help_text in found:
            self.add_option(Option(f"/{name}   {help_text}", id=name))
        self.highlighted = 0
        self.display = True

    def hide(self) -> None:
        self.display = False
        self.clear_options()

    @property
    def selected(self) -> str | None:
        """The command name under the cursor, or None."""
        if not self.display or self.highlighted is None:
            return None
        try:
            return self.get_option_at_index(self.highlighted).id
        except Exception:
            return None

    def move(self, delta: int) -> None:
        """Wrap rather than stop: a five-item menu is faster to cycle than to
        walk to the end of."""
        count = self.option_count
        if not count:
            return
        current = 0 if self.highlighted is None else self.highlighted
        self.highlighted = (current + delta) % count
