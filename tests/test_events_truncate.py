import dataclasses
import typing

import pytest

from pyrrhon.core.events import Event, TruncateSpeech


def test_truncate_speech_is_a_frozen_value():
    event = TruncateSpeech(played_text="Auth starts in the middleware")
    assert event.played_text == "Auth starts in the middleware"
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.played_text = "something else"


def test_truncate_speech_is_part_of_the_event_union():
    assert TruncateSpeech in typing.get_args(Event)
