"""The event-dispatch table is complete, and both channels route through it.

The REPL and the TUI each carried their own isinstance ladder over the event
stream until these were unified, and the two had already drifted: the TUI
rendered ScreenArtifact and the REPL dropped it silently, because a missing
`elif` looks exactly like a deliberate no-op. These tests make the drift
loud — the first one fails the moment an event type is added to the union
without a hook.
"""

import typing

import pytest

from pyrrhon.channels import EVENT_HOOKS, EventRenderer
from pyrrhon.core import events as events_mod

UNION_MEMBERS = set(typing.get_args(events_mod.Event))


def test_every_event_in_the_union_has_a_hook():
    assert set(EVENT_HOOKS) == UNION_MEMBERS


def test_every_hook_exists_on_the_base_renderer():
    """A typo'd hook name would otherwise surface as AttributeError mid-turn."""
    for event_type, hook in EVENT_HOOKS.items():
        assert hasattr(EventRenderer, hook), f"{event_type.__name__} -> {hook}"


def test_base_renderer_drops_every_event_without_raising():
    """The defaults are no-ops: a channel implements only what it displays."""
    renderer = EventRenderer()
    renderer.render(events_mod.SpeechChunk(text="hi"))
    renderer.render(events_mod.Citation(file="a.py", line=1))
    renderer.render(events_mod.ScreenArtifact(kind="markdown", content="# x"))


def test_unknown_event_type_is_ignored_not_raised():
    """A channel dropping an unrecognised event is survivable; a channel
    crashing mid-turn is not."""
    EventRenderer().render(object())


def test_render_routes_to_the_matching_hook():
    seen = []

    class Probe(EventRenderer):
        def on_speech(self, event):
            seen.append(("speech", event.text))

        def on_artifact(self, event):
            seen.append(("artifact", event.content))

    probe = Probe()
    probe.render(events_mod.SpeechChunk(text="spoken"))
    probe.render(events_mod.ScreenArtifact(kind="markdown", content="shown"))
    assert seen == [("speech", "spoken"), ("artifact", "shown")]


@pytest.mark.parametrize(
    "renderer_path",
    ["pyrrhon.repl.ConsoleRenderer", "pyrrhon.tui.app.TuiRenderer"],
)
def test_channel_renderers_derive_from_the_shared_base(renderer_path):
    """Pins the unification itself: a channel that grows its own isinstance
    ladder again would stop inheriting these guarantees."""
    module_name, _, attr = renderer_path.rpartition(".")
    module = __import__(module_name, fromlist=[attr])
    assert issubclass(getattr(module, attr), EventRenderer)


def test_the_repl_renders_screen_artifacts():
    """The regression this unification fixes. ScreenArtifact reached the TUI
    and vanished on the REPL; M14's orientation brief is its first emitter and
    anything emitting one mid-turn was invisible in text mode."""
    from pyrrhon.repl import ConsoleRenderer

    assert "on_artifact" in vars(ConsoleRenderer)
