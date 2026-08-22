"""Slash commands in Textual's own command palette (D4).

No new dependency and no second copy of the command list. The provider reads
the registry at search time, so a plugin that registers a command after
startup is findable on ctrl+p without anything being told about it, and every
hit dispatches through the same `dispatch()` the prompt uses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Awaitable

from textual.command import DiscoveryHit, Hit, Hits, Provider

from pyrrhon.commands.registry import all_commands


class PyrrhonCommands(Provider):
    """One palette hit per registered slash command."""

    def _runner(self, name: str) -> Callable[[], Awaitable[None]]:
        app = self.app

        async def run() -> None:
            # run_command, not dispatch: the app renders the response and
            # refreshes the status bar, which is the whole execution path.
            await app.run_command(f"/{name}")

        return run

    async def discover(self) -> Hits:
        """What the palette lists before anything is typed."""
        for cmd in all_commands():
            yield DiscoveryHit(
                f"/{cmd.name}",
                self._runner(cmd.name),
                help=cmd.help_text,
            )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for cmd in all_commands():
            name = f"/{cmd.name}"
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    self._runner(cmd.name),
                    help=cmd.help_text,
                )
