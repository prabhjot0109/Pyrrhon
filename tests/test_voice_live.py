"""Tier 3: does the provider actually work? Opt-in, keys required, not in CI.

Run one:   uv run pytest tests/test_voice_live.py -m live -k piper -v
Run all:   uv run pytest tests/test_voice_live.py -m live -v

Tiers 1 and 2 (tests/test_voice_registry.py) prove a provider's class exists
and that its extra is installable. Neither can tell you the class still works
against the live service — this is the ONLY tier that catches a retired model
id or a changed auth scheme. Record the results in the plan's Implementation
Record before a release.
"""

import os

import pytest

from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.factory import VoiceUnavailableError, create_tts
from pyrrhon.voice.registry import tts_providers

pytestmark = pytest.mark.live


@pytest.mark.parametrize("provider", tts_providers(), ids=lambda p: p.id)
def test_tts_provider_constructs_and_synthesizes(provider):
    if provider.key_env and not os.environ.get(provider.key_env):
        pytest.skip(f"{provider.key_env} not set")
    if provider.requires_voice or provider.requires_model:
        pytest.skip(f"{provider.id} needs an account-specific id; set it manually")
    try:
        service = create_tts(VoiceSettings(tts_provider=provider.id))
    except VoiceUnavailableError as exc:
        pytest.skip(str(exc))
    assert service is not None
