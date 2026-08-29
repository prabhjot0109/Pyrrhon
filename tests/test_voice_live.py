"""Tier 3: does the provider actually work? Opt-in, keys required, not in CI.

Run one:   uv run pytest tests/test_voice_live.py -m live -k piper -v
Run all:   uv run pytest tests/test_voice_live.py -m live -v

Tiers 1 and 2 (tests/test_voice_registry.py) prove a provider's class exists
and that its extra is installable. Neither can tell you the class still works
against the live service — this is the ONLY tier that catches a retired model
id or a changed auth scheme. Record the results in the plan's Implementation
Record before a release, and set `verified=` on the rows that passed: that flag
is what `catalog.availability()` renders, so a row nobody has smoke-tested says
so in the menu instead of claiming to be ready.

What "works" means here is one real utterance, end to end:

  TTS — speak a sentence, count the TTSAudioRawFrames that come back.
  STT — transcribe that same audio, and check the words survived the round trip.

Both run inside pipecat's own `run_test` harness rather than a hand-rolled
driver, which is the Layer C rule applied to the test suite: the harness starts
the service properly, so `AIService.start`'s `validate_complete()` runs and the
settings a row ships are checked, not just the constructor call.

The speech the STT half transcribes is produced by Piper, the keyless bundled
TTS. That keeps a binary fixture out of the repo and makes the pass mean
something stronger than either half alone: Pyrrhon's own voice out is
intelligible to Pyrrhon's own voice in.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from pyrrhon.config.credentials import load_credentials
from pyrrhon.config.settings import VoiceSettings
from pyrrhon.voice.factory import VoiceUnavailableError, create_stt, create_tts
from pyrrhon.voice.registry import stt_providers, tts_providers

pytestmark = pytest.mark.live

PHRASE = "The quick brown fox jumps over the lazy dog."
_KEYWORDS = {"quick", "brown", "fox", "jumps", "lazy", "dog"}

# What every STT row on the table is fed. 16 kHz mono 16-bit is the format
# every hosted STT accepts, so the fixture resamples Piper's output to it once
# rather than negotiating per provider.
SAMPLE_RATE = 16000
_CHUNK_BYTES = SAMPLE_RATE * 2 // 50  # 20 ms, the size a transport emits


@pytest.fixture(scope="session", autouse=True)
def _credentials():
    """Count a key set by `pyrrhon --setup`, not just one exported in the shell.

    Without this the tier reads os.environ only and skips every provider whose
    key lives in ~/.pyrrhon/credentials.toml — which is where the app itself
    reads them from, so the run would report "no key" for providers the user
    demonstrably has. The evals already do exactly this.
    """
    load_credentials()


def _live_settings(provider) -> VoiceSettings:
    """What to construct `provider` with, or a skip explaining why we cannot.

    Rows needing an account-specific voice or model id (Cartesia, ElevenLabs,
    Groq TTS, HF) cannot be smoke-tested from the table alone. Rather than
    skipping them all, this borrows the id from the user's own [voice] config
    — but ONLY when they configured this same provider, because a Cartesia
    voice id sent to ElevenLabs is a 400 dressed up as a test failure.

    That makes the tier machine-dependent, which is the point of it: tier 3 is
    the "against the providers you actually have" tier, and the provider a user
    runs every day is the one whose breakage would hurt most.
    """
    from pyrrhon.config.settings import load_settings

    if provider.key_env and not os.environ.get(provider.key_env):
        pytest.skip(f"{provider.key_env} not set")

    configured = load_settings(pathlib.Path.cwd()).voice
    mine = getattr(configured, f"{provider.kind}_provider") == provider.id
    model = (configured.stt_model if provider.kind == "stt" else configured.tts_model)
    voice = configured.tts_voice
    if provider.requires_voice and not (mine and voice):
        pytest.skip(
            f"{provider.id} needs a voice id from your account; set [voice] "
            f"tts_provider = \"{provider.id}\" and tts_voice to smoke-test it"
        )
    if provider.requires_model and not (mine and model):
        pytest.skip(
            f"{provider.id} needs an explicit model id; set [voice] "
            f"{provider.kind}_provider = \"{provider.id}\" and "
            f"{provider.kind}_model to smoke-test it"
        )
    if provider.kind == "stt":
        return VoiceSettings(
            stt_provider=provider.id, stt_model=model if mine else None
        )
    return VoiceSettings(
        tts_provider=provider.id,
        tts_model=model if mine else None,
        tts_voice=voice if mine else None,
    )


async def _speak(service, text: str, sample_rate: int) -> list[bytes]:
    """Push one utterance through `service` and collect the audio it produced."""
    from pipecat.frames.frames import TTSAudioRawFrame, TTSSpeakFrame
    from pipecat.pipeline.worker import PipelineParams
    from pipecat.tests.utils import SleepFrame, run_test

    downstream, _ = await run_test(
        service,
        frames_to_send=[TTSSpeakFrame(text), SleepFrame(20.0)],
        pipeline_params=PipelineParams(audio_out_sample_rate=sample_rate),
    )
    return [f.audio for f in downstream if isinstance(f, TTSAudioRawFrame) and f.audio]


@pytest.fixture(scope="session")
def speech_audio() -> bytes:
    """PHRASE as 16 kHz mono PCM, spoken by Piper. Session-scoped: it is a
    one-off model download plus a synth, and every STT row reuses it."""
    import asyncio

    try:
        piper = create_tts(VoiceSettings(tts_provider="piper"))
    except VoiceUnavailableError as exc:
        pytest.skip(f"no keyless TTS to generate speech with: {exc}")
    chunks = asyncio.run(_speak(piper, PHRASE, SAMPLE_RATE))
    if not chunks:
        pytest.skip("Piper produced no audio; the STT tier has nothing to send")
    return b"".join(chunks)


@pytest.mark.parametrize("provider", tts_providers(), ids=lambda p: p.id)
async def test_tts_provider_speaks_one_utterance(provider):
    """Construction proves nothing: a retired model id constructs fine and
    fails on the first synthesis. Audio frames are the evidence."""
    settings = _live_settings(provider)
    try:
        service = create_tts(settings)
    except VoiceUnavailableError as exc:
        pytest.skip(str(exc))

    chunks = await _speak(service, PHRASE, SAMPLE_RATE)
    assert chunks, f"{provider.id} returned no audio for a plain sentence"
    assert sum(len(c) for c in chunks) > SAMPLE_RATE // 2, (
        f"{provider.id} returned under a quarter second of audio"
    )


@pytest.mark.parametrize("provider", stt_providers(), ids=lambda p: p.id)
async def test_stt_provider_transcribes_one_utterance(provider, speech_audio):
    """The half the tier was missing entirely.

    Bracketed by VAD frames because segmented services (groq, openai, and the
    two in-repo shims) transcribe on VADUserStoppedSpeakingFrame; streaming
    services ignore the brackets and answer as the audio arrives, which is what
    the trailing sleep waits for.

    The LEADING sleep is the one that is easy to delete and must not be. A
    streaming service opens its socket from StartFrame, and Deepgram's
    _connect() only launches that handshake as a background task — its
    run_stt drops audio while _connection is still None. run_test delivers
    all 2.6 seconds in about three milliseconds, so without a pause the whole
    utterance lands before the socket exists and the service is failed for
    something no microphone could ever do to it.
    """
    settings = _live_settings(provider)
    try:
        service = create_stt(settings)
    except VoiceUnavailableError as exc:
        pytest.skip(str(exc))

    from pipecat.frames.frames import (
        InputAudioRawFrame,
        TranscriptionFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.worker import PipelineParams
    from pipecat.tests.utils import SleepFrame, run_test

    audio_frames = [
        InputAudioRawFrame(
            audio=speech_audio[at : at + _CHUNK_BYTES],
            sample_rate=SAMPLE_RATE,
            num_channels=1,
        )
        for at in range(0, len(speech_audio), _CHUNK_BYTES)
    ]
    downstream, _ = await run_test(
        service,
        frames_to_send=[
            SleepFrame(2.0),
            VADUserStartedSpeakingFrame(),
            *audio_frames,
            VADUserStoppedSpeakingFrame(),
            SleepFrame(20.0),
        ],
        pipeline_params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE),
    )

    heard = " ".join(
        f.text for f in downstream if isinstance(f, TranscriptionFrame) and f.text
    )
    assert heard.strip(), f"{provider.id} transcribed nothing"
    words = set(heard.lower().replace(".", "").replace(",", "").split())
    assert len(words & _KEYWORDS) >= 2, (
        f"{provider.id} heard {heard!r}, which does not resemble {PHRASE!r}"
    )


# -- The other live check that has never been run ----------------------------

async def test_read_image_describes_a_real_diagram(tmp_path):
    """read_image against the configured [vision] slot and a real image.

    Twelve unit tests drive this tool against a fake vision LLM, which proves
    the plumbing and nothing about whether a model can actually see the bytes
    we send. Same category of gap as asserting a TTS service is not None, and
    it belongs in the same pre-release pass.
    """
    from pyrrhon.config.settings import load_settings
    from pyrrhon.core.providers.llm import create_llm
    from pyrrhon.core.tools.images import ReadImageTool

    settings = load_settings(pathlib.Path.cwd())
    slot = settings.vision_slot()
    if slot is None:
        pytest.skip(
            "no vision slot: set [vision] provider/model, or point [fast] at a "
            "provider the table marks vision-capable"
        )

    image = tmp_path / "arch.png"
    _draw_diagram(image)
    tool = ReadImageTool(tmp_path, create_llm(slot, settings))
    answer = await tool.run(path="arch.png", question="What boxes does this show?")

    assert not answer.startswith("ERROR:"), answer
    lowered = answer.lower()
    seen = [box for box in ("client", "gateway", "database") if box in lowered]
    assert len(seen) >= 2, f"the model read {answer!r} off a three-box diagram"


def _draw_diagram(path) -> None:
    """A three-box architecture diagram — the exact shape read_image is for."""
    image_mod = pytest.importorskip("PIL.Image", reason="needs pillow")
    draw_mod = pytest.importorskip("PIL.ImageDraw", reason="needs pillow")

    canvas = image_mod.new("RGB", (720, 200), "white")
    pen = draw_mod.Draw(canvas)
    for index, label in enumerate(("CLIENT", "GATEWAY", "DATABASE")):
        left = 30 + index * 230
        pen.rectangle([left, 60, left + 180, 140], outline="black", width=4)
        pen.text((left + 40, 95), label, fill="black")
        if index:
            pen.line([left - 50, 100, left, 100], fill="black", width=4)
    canvas.save(path)
