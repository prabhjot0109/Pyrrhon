# Pyrrhon M9 — Provider Expansion, Onboarding Wizard, Product Polish

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Pyrrhon's LLM/STT/TTS provider matrix (DeepSeek, Hugging Face, Gemini voice via API key, better local models), add a first-run onboarding wizard with safe API-key storage, lock down the safety invariants with tests, and ship product polish (branding banner, `/settings`, `.env.example`).

**Architecture:** All LLM providers ride the existing OpenAI-compatible adapter (`OpenAICompatLLM`) — new providers are config entries, not code. Voice providers extend the existing lazy-import registry in `pyrrhon/voice/providers.py`; Gemini STT/TTS are thin custom pipecat services in a new `pyrrhon/voice/gemini.py` using the `google-genai` SDK with a plain `GEMINI_API_KEY` (pipecat's own Google services demand Cloud service-account credentials, which we reject as a setup burden). The wizard runs in the plain terminal before any channel starts (same spot as the plugin-consent prompt), writes provider choices into `~/.pyrrhon/config.toml` and keys into a separate `~/.pyrrhon/credentials.toml` (chmod 600, env vars always win).

**Tech Stack:** Python ≥3.12, uv, pydantic v2, pipecat-ai 1.5.0, openai SDK, rich, Textual, pytest (asyncio_mode=auto). New deps: `google-genai`, `tomli-w`.

## Global Constraints

- Python `>=3.12` (`pyproject.toml`); manage deps only via `uv add` / `uv sync`.
- Run tests with `uv run pytest`; a single test with `uv run pytest path::test_name -v`.
- **Grounding is a hard requirement** (CLAUDE.md): never add a path that lets the agent speak unverified claims or bypass the `GroundingGate`.
- **Voice provider factories must fail with `VoiceUnavailableError` BEFORE importing any pipecat service module** — missing key / unknown provider / missing voice id all degrade to text mode with an actionable message (M3 error policy, pinned by `tests/test_voice_providers.py`).
- **Never print, log, or echo an API key value** — not in wizard output, not in `/settings`, not in error messages. Key input uses `getpass` (hidden).
- **Environment variables always win over stored credentials** (`os.environ.setdefault` when loading the credentials file).
- Existing 259 tests must stay green after every task. No lint config exists — match surrounding style (double quotes, `from __future__ import annotations`, module docstrings explaining *why*).
- Tests never import pipecat service classes or `google.genai` for real — fake them via `sys.modules` (existing pattern: key checks fail before import).
- Commit after every task with a conventional-commit message; never `--no-verify`.
- **Parked, do not build** (scope discipline): Gemini Live speech-to-speech (bypasses the Agent loop and grounding gate — documented in Task 13), LangChain integration (the OpenAI-compat adapter already covers HF/Ollama/LM Studio), Kokoro TTS, plugin marketplace.

## File Structure

| File | Responsibility |
|---|---|
| `pyrrhon/config/settings.py` (modify) | +`deepseek`/`huggingface` builtin providers; `VoiceSettings` per-provider defaults (`stt_model`/`tts_voice` become `None`) |
| `pyrrhon/voice/providers.py` (modify) | STT/TTS registry: gemini entries, whisper-local model passthrough, in-process piper, per-provider defaults |
| `pyrrhon/voice/gemini.py` (create) | `GeminiSTTService` + `GeminiTTSService` (google-genai, API-key auth) |
| `pyrrhon/config/credentials.py` (create) | Read/write `~/.pyrrhon/credentials.toml` (0600); load into env |
| `pyrrhon/config/catalog.py` (create) | `ProviderChoice` data: every LLM/STT/TTS choice the wizard and `/settings` show |
| `pyrrhon/config/wizard.py` (create) | Navigable numbered-menu setup wizard; `needs_setup`/`ensure_configured` startup hooks |
| `pyrrhon/branding.py` (create) | ASCII mascot banner + tagline |
| `pyrrhon/commands/settings_cmd.py` (create) | `/settings` — providers, models, masked key status |
| `pyrrhon/cli.py` (modify) | `--setup` flag |
| `pyrrhon/repl.py`, `pyrrhon/tui/app.py` (modify) | call `ensure_configured` at startup; render banner; register `/settings` |
| `.env.example` (create) | Every supported env var, commented |
| `README.md`, `CLAUDE.md` (modify) | Provider matrix, wizard docs, security model, Gemini Live parked note |
| `tests/test_settings.py`, `tests/test_voice_providers.py` (modify) | extended coverage |
| `tests/test_credentials.py`, `tests/test_catalog.py`, `tests/test_wizard.py`, `tests/test_branding.py`, `tests/test_settings_cmd.py`, `tests/test_safety.py`, `tests/test_gemini_voice.py` (create) | new coverage |

**Provider gap analysis (what drove the tasks):**

| Requested | Status today | Action |
|---|---|---|
| Gemini LLM reasoning | ✅ builtin (`gemini`, OpenAI-compat endpoint) | expose in wizard |
| DeepSeek LLM | ❌ | Task 1 |
| Hugging Face LLM (router API) | ❌ | Task 1 |
| Cerebras / Ollama / LM Studio / OpenRouter | ✅ builtin | expose in wizard |
| HF local small LLMs | ✅ via ollama/lmstudio | document (Task 13) |
| Local whisper small models (tiny/base/small/distil, HF ids) | ⚠️ `whisper-local` exists but ignores `stt_model` | Task 2 |
| Piper TTS | ⚠️ HTTP-server variant only | Task 3 (in-process, zero-setup) |
| Gemini STT via API | ❌ | Task 4 |
| Gemini TTS via API | ❌ | Task 5 |
| Gemini Live speech-to-speech | ❌ | **parked** — bypasses grounding gate (Task 13 documents why) |
| OpenClaw-style onboarding + safe key storage | ❌ | Tasks 6–9 |
| Safety: no dangerous command execution | ✅ by architecture (read-only git allowlist, `write_spec` confined) | Task 12 pins it with tests |
| Claude-Code-like polish, logo/mascot | ❌ | Tasks 10–11 |
| `.env.example` | ❌ | Task 13 |

---

### Task 1: DeepSeek and Hugging Face builtin LLM providers

**Files:**
- Modify: `pyrrhon/config/settings.py:38-55` (the `BUILTIN_PROVIDERS` dict)
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `ProviderConfig`, `BUILTIN_PROVIDERS`, `Settings.provider_for` (all existing in `pyrrhon/config/settings.py`)
- Produces: `BUILTIN_PROVIDERS["deepseek"]` and `BUILTIN_PROVIDERS["huggingface"]` — Task 7's catalog and the wizard rely on these exact ids.

- [ ] **Step 1: Write the failing test** — append to `tests/test_settings.py`:

```python
def test_deepseek_and_huggingface_are_builtin_providers():
    settings = Settings()
    deepseek = settings.provider_for(ModelSlot(provider="deepseek", model="deepseek-chat"))
    assert deepseek.base_url == "https://api.deepseek.com/v1"
    assert deepseek.api_key_env == "DEEPSEEK_API_KEY"
    hf = settings.provider_for(
        ModelSlot(provider="huggingface", model="meta-llama/Llama-3.3-70B-Instruct")
    )
    assert hf.base_url == "https://router.huggingface.co/v1"
    assert hf.api_key_env == "HF_TOKEN"
```

(`Settings` and `ModelSlot` are already imported at the top of `tests/test_settings.py`; if not, add them.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings.py::test_deepseek_and_huggingface_are_builtin_providers -v`
Expected: FAIL with `KeyError: "Unknown provider 'deepseek'..."`

- [ ] **Step 3: Write minimal implementation** — add two entries to `BUILTIN_PROVIDERS` in `pyrrhon/config/settings.py`, after the `"cerebras"` entry:

```python
    "deepseek": ProviderConfig(
        base_url="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY"
    ),
    "huggingface": ProviderConfig(
        base_url="https://router.huggingface.co/v1", api_key_env="HF_TOKEN"
    ),
```

- [ ] **Step 4: Run the full settings tests**

Run: `uv run pytest tests/test_settings.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/config/settings.py tests/test_settings.py
git commit -m "feat(providers): DeepSeek and Hugging Face router as builtin LLM providers"
```

---

### Task 2: Per-provider STT model / TTS voice defaults

Today `VoiceSettings.stt_model` defaults to `"whisper-large-v3-turbo"` (a Groq model) and is passed verbatim to *every* STT provider — so `stt_provider = "openai"` sends OpenAI an invalid model name (latent bug), and `whisper-local` ignores the setting entirely (can't pick tiny/base/small/distil models). Same story for `tts_voice = "nova"` (OpenAI-specific) being sent to Cartesia/ElevenLabs as a voice id. Fix: defaults become `None`; each provider branch supplies its own default or requires an explicit value.

**Files:**
- Modify: `pyrrhon/config/settings.py:58-74` (`VoiceSettings`)
- Modify: `pyrrhon/voice/providers.py:42-131` (`create_stt`, `create_tts`)
- Test: `tests/test_voice_providers.py`, `tests/test_settings.py:91` (default assertion changes)

**Interfaces:**
- Consumes: `VoiceSettings`, `create_stt(voice)`, `create_tts(voice)`, `VoiceUnavailableError` (existing).
- Note (verified against the working tree): `pyrrhon/voice/pipeline.py:55-56` already builds its services via `create_stt(voice)` / `create_tts(voice)` — the registry IS the single factory, so no channel or pipeline changes are needed in this task or any later one. If you are looking at an older snapshot that hardcodes `GroqSTTService`/`OpenAITTSService` inline, you are not on `main`.
- Produces: `VoiceSettings.stt_model: str | None = None`, `VoiceSettings.tts_voice: str | None = None`. Semantics later tasks rely on: when `None`, groq STT uses `whisper-large-v3-turbo`, openai STT/whisper-local use the pipecat default, openai TTS uses `nova`, deepgram TTS uses `aura-2-thalia-en`, cartesia/elevenlabs raise `VoiceUnavailableError` demanding an explicit voice id. Gemini defaults land in Tasks 4–5.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_voice_providers.py`. The fake-module helper is the pattern all later voice tests reuse — put it at module level:

```python
import sys
import types


def _fake_service(captured: dict):
    """A stand-in pipecat service class that records its constructor kwargs."""

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    return FakeService


def _install_fake(monkeypatch, module_name: str, class_name: str, captured: dict):
    module = types.ModuleType(module_name)
    setattr(module, class_name, _fake_service(captured))
    monkeypatch.setitem(sys.modules, module_name, module)


def test_whisper_local_passes_the_configured_model(monkeypatch):
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.whisper.stt", "WhisperSTTService", captured)
    create_stt(VoiceSettings(stt_provider="whisper-local", stt_model="distil-medium.en"))
    assert captured == {"model": "distil-medium.en"}


def test_whisper_local_uses_pipecat_default_when_model_unset(monkeypatch):
    captured: dict = {"untouched": True}
    _install_fake(monkeypatch, "pipecat.services.whisper.stt", "WhisperSTTService", captured)
    create_stt(VoiceSettings(stt_provider="whisper-local"))
    assert captured == {"untouched": True}  # no kwargs passed


def test_groq_stt_defaults_to_whisper_large_v3_turbo(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.groq.stt", "GroqSTTService", captured)
    create_stt(VoiceSettings(stt_provider="groq"))
    assert captured["model"] == "whisper-large-v3-turbo"


def test_openai_stt_no_longer_sends_a_groq_model_name(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.openai.stt", "OpenAISTTService", captured)
    create_stt(VoiceSettings(stt_provider="openai"))
    assert "model" not in captured  # pipecat's own default applies


def test_cartesia_requires_an_explicit_voice_id(monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "k")
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="cartesia"))
    assert "tts_voice" in str(exc.value)


def test_openai_tts_defaults_to_nova(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.openai.tts", "OpenAITTSService", captured)
    create_tts(VoiceSettings(tts_provider="openai"))
    assert captured["voice"] == "nova"


def test_deepgram_tts_defaults_to_aura_thalia(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.deepgram.tts", "DeepgramTTSService", captured)
    create_tts(VoiceSettings(tts_provider="deepgram"))
    assert captured["voice"] == "aura-2-thalia-en"
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_voice_providers.py -v`
Expected: the new tests FAIL (whisper-local receives no model; openai STT receives `whisper-large-v3-turbo`; cartesia builds with voice `"nova"`).

- [ ] **Step 3: Implement.** In `pyrrhon/config/settings.py`, change `VoiceSettings`:

```python
class VoiceSettings(BaseModel):
    """M3/M8/M9 voice-channel knobs (TOML section [voice]).

    stt_provider: groq | openai | gemini | deepgram | whisper-local
    tts_provider: openai | gemini | cartesia | elevenlabs | deepgram | piper
    stt_model / tts_voice are provider-specific; when unset each provider
    applies its own sensible default (see pyrrhon/voice/providers.py).
    Cartesia and ElevenLabs have no meaningful default voice — they require
    an explicit tts_voice (a voice id from your account).
    """

    stt_provider: str = "groq"
    stt_model: str | None = None               # provider default when unset
    tts_provider: str = "openai"
    tts_model: str | None = None               # provider default when unset
    tts_voice: str | None = None               # provider default when unset
    tts_url: str | None = None                 # local TTS server (piper HTTP mode)
    chars_per_sec: float = 15.0                # played-text estimator rate
```

In `pyrrhon/voice/providers.py`, update the branches (key checks stay first, still before any pipecat import):

```python
def create_stt(voice: VoiceSettings):
    provider = voice.stt_provider
    if provider == "groq":
        key = _key("GROQ_API_KEY", "Groq Whisper STT")
        try:
            from pipecat.services.groq.stt import GroqSTTService
        except ImportError as exc:
            raise _import_error(exc, "groq") from exc
        return GroqSTTService(api_key=key, model=voice.stt_model or "whisper-large-v3-turbo")
    if provider == "openai":
        key = _key("OPENAI_API_KEY", "OpenAI STT")
        try:
            from pipecat.services.openai.stt import OpenAISTTService
        except ImportError as exc:
            raise _import_error(exc, "openai") from exc
        kwargs = {"api_key": key}
        if voice.stt_model:
            kwargs["model"] = voice.stt_model
        return OpenAISTTService(**kwargs)
    ...
    if provider == "whisper-local":
        try:
            from pipecat.services.whisper.stt import WhisperSTTService
        except ImportError as exc:
            raise _import_error(exc, "whisper") from exc
        # faster-whisper, runs locally, no key. stt_model picks the size:
        # tiny | base | small | medium | large-v3, or an HF id such as
        # "Systran/faster-distil-whisper-medium.en".
        if voice.stt_model:
            return WhisperSTTService(model=voice.stt_model)
        return WhisperSTTService()
```

For TTS: `openai` uses `"voice": voice.tts_voice or "nova"`; `deepgram` uses `voice=voice.tts_voice or "aura-2-thalia-en"`; `cartesia` and `elevenlabs` add, after the key check and **before** the import:

```python
        if not voice.tts_voice:
            raise VoiceUnavailableError(
                f"{provider} TTS needs tts_voice set to one of your voice ids "
                "in [voice] — staying in text mode."
            )
```

Update `tests/test_settings.py:91` from `assert settings.voice.tts_voice == "nova"` to `assert settings.voice.tts_voice is None`.

- [ ] **Step 4: Run the full suite** (this change touches shared defaults)

Run: `uv run pytest`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/config/settings.py pyrrhon/voice/providers.py tests/test_voice_providers.py tests/test_settings.py
git commit -m "fix(voice): per-provider STT model and TTS voice defaults; whisper-local model selection"
```

---

### Task 3: In-process Piper TTS (zero-setup local voice)

Pipecat 1.5.0 ships `PiperTTSService` — fully local, downloads the voice model itself, no HTTP server to run. Today Pyrrhon only wires `PiperHttpTTSService`, which requires the user to run `piper --http` separately. Make in-process the default; keep HTTP mode when `tts_url` is set.

**Files:**
- Modify: `pyrrhon/voice/providers.py` (the `piper` branch of `create_tts`)
- Test: `tests/test_voice_providers.py`

**Interfaces:**
- Consumes: `VoiceSettings.tts_url`, `VoiceSettings.tts_voice` (Task 2 semantics).
- Produces: `piper` provider behavior — `tts_url` set → `PiperHttpTTSService(base_url=...)`; unset → `PiperTTSService(voice_id=tts_voice or "en_US-lessac-medium", download_dir=~/.pyrrhon/piper)`. The wizard (Task 8) describes piper as "local, no key, no server".

- [ ] **Step 1: Write the failing tests** — append to `tests/test_voice_providers.py` (reuses `_install_fake`):

```python
def test_piper_runs_in_process_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured: dict = {}
    _install_fake(monkeypatch, "pipecat.services.piper.tts", "PiperTTSService", captured)
    create_tts(VoiceSettings(tts_provider="piper"))
    assert captured["voice_id"] == "en_US-lessac-medium"
    assert captured["download_dir"] == tmp_path / ".pyrrhon" / "piper"


def test_piper_uses_http_service_when_tts_url_is_set(monkeypatch):
    captured: dict = {}
    module = types.ModuleType("pipecat.services.piper.tts")
    module.PiperHttpTTSService = _fake_service(captured)
    module.PiperTTSService = _fake_service({})
    monkeypatch.setitem(sys.modules, "pipecat.services.piper.tts", module)
    create_tts(VoiceSettings(tts_provider="piper", tts_url="http://localhost:5000"))
    assert captured["base_url"] == "http://localhost:5000"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_voice_providers.py -k piper -v`
Expected: FAIL (current code always builds `PiperHttpTTSService`)

- [ ] **Step 3: Implement** — replace the `piper` branch in `create_tts`:

```python
    if provider == "piper":
        if voice.tts_url:
            # Explicit server mode: talk to a running `piper --http`.
            try:
                from pipecat.services.piper.tts import PiperHttpTTSService
            except ImportError as exc:
                raise _import_error(exc, "piper") from exc
            import aiohttp

            return PiperHttpTTSService(
                base_url=voice.tts_url, aiohttp_session=aiohttp.ClientSession()
            )
        # Default: in-process Piper — downloads the voice model on first use,
        # nothing else to run. Local, free, keyless.
        try:
            from pipecat.services.piper.tts import PiperTTSService
        except ImportError as exc:
            raise _import_error(exc, "piper") from exc
        from pathlib import Path

        return PiperTTSService(
            voice_id=voice.tts_voice or "en_US-lessac-medium",
            download_dir=Path.home() / ".pyrrhon" / "piper",
        )
```

(Put `from pathlib import Path` at module top instead of inline — match file style.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_voice_providers.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/voice/providers.py tests/test_voice_providers.py
git commit -m "feat(voice): in-process Piper TTS by default — no local server needed"
```

---

### Task 4: Gemini STT via API key

Pipecat's `GoogleSTTService` requires Google **Cloud** service-account credentials. Pyrrhon's promise is "paste one API key", so we implement a thin segmented STT service over the Gemini API (`google-genai` SDK, `GEMINI_API_KEY`). It subclasses pipecat's `BaseWhisperSTTService` and overrides only `_transcribe` — the base class already handles VAD-segmented WAV audio, metrics, and `TranscriptionFrame` emission (this is exactly how `GroqSTTService` is built).

**Files:**
- Create: `pyrrhon/voice/gemini.py`
- Modify: `pyrrhon/voice/providers.py` (`STT_PROVIDERS`, new branch in `create_stt`)
- Test: `tests/test_gemini_voice.py` (create), `tests/test_voice_providers.py`

**Interfaces:**
- Consumes: `pipecat.services.whisper.base_stt.BaseWhisperSTTService` (override `async _transcribe(self, audio: bytes) -> Transcription`; `audio` is a WAV-encoded segment; `Transcription` is `openai.types.audio.Transcription`, a pydantic model with a `text` field — verified against the installed pipecat 1.5.0 sources).
- Produces: `GeminiSTTService(api_key: str, model: str = "gemini-2.5-flash")` in `pyrrhon/voice/gemini.py`; `"gemini"` in `STT_PROVIDERS`. Task 7's catalog lists `gemini` STT with default model `gemini-2.5-flash`.

- [ ] **Step 1: Add the dependency**

Run: `uv add google-genai`
Expected: resolves and syncs without conflicts.

- [ ] **Step 2: Write the failing registry tests** — append to `tests/test_voice_providers.py`:

```python
def test_gemini_stt_requires_gemini_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_stt(VoiceSettings(stt_provider="gemini"))
    assert "GEMINI_API_KEY" in str(exc.value)


def test_gemini_stt_builds_with_key_and_default_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pyrrhon.voice.gemini", "GeminiSTTService", captured)
    create_stt(VoiceSettings(stt_provider="gemini"))
    assert captured == {"api_key": "k", "model": "gemini-2.5-flash"}
```

And create `tests/test_gemini_voice.py` for the service itself (fake `google.genai`, no network):

```python
"""GeminiSTTService/GeminiTTSService — thin google-genai wrappers, no network in tests."""

import sys
import types
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def fake_genai(monkeypatch):
    """Install a fake `google.genai` package; return the client instance.

    pyrrhon.voice.gemini binds `genai` at import time, so it must be evicted
    from the module cache FIRST — otherwise a prior import (real SDK) would
    keep pointing at the real client and _transcribe would hit the network.
    """
    monkeypatch.delitem(sys.modules, "pyrrhon.voice.gemini", raising=False)
    client = types.SimpleNamespace()
    client.aio = types.SimpleNamespace(models=types.SimpleNamespace())

    genai = types.ModuleType("google.genai")
    genai.Client = lambda api_key: client
    genai_types = types.ModuleType("google.genai.types")

    class _Passthrough:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    for name in (
        "GenerateContentConfig", "SpeechConfig", "VoiceConfig", "PrebuiltVoiceConfig"
    ):
        setattr(genai_types, name, type(name, (_Passthrough,), {}))
    genai_types.Part = types.SimpleNamespace(
        from_bytes=lambda data, mime_type: {"data": data, "mime_type": mime_type}
    )
    google = types.ModuleType("google")
    google.genai = genai
    genai.types = genai_types
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)
    return client


async def test_transcribe_sends_wav_and_returns_text(fake_genai):
    from pyrrhon.voice.gemini import GeminiSTTService

    response = types.SimpleNamespace(text="  hello world  ")
    fake_genai.aio.models.generate_content = AsyncMock(return_value=response)

    service = GeminiSTTService(api_key="k", model="gemini-2.5-flash")
    result = await service._transcribe(b"RIFF-fake-wav")

    assert result.text == "hello world"
    call = fake_genai.aio.models.generate_content.call_args
    assert call.kwargs["model"] == "gemini-2.5-flash"
    assert call.kwargs["contents"][0]["mime_type"] == "audio/wav"
```

- [ ] **Step 3: Run to verify failures**

Run: `uv run pytest tests/test_gemini_voice.py tests/test_voice_providers.py -k gemini -v`
Expected: FAIL with `ModuleNotFoundError: pyrrhon.voice.gemini` / unknown provider errors.

- [ ] **Step 4: Implement.** Create `pyrrhon/voice/gemini.py`:

```python
"""Gemini voice services over the plain Gemini API key.

Pipecat's own Google services (GoogleSTTService, GeminiTTSService) require
Google Cloud service-account credentials. Pyrrhon's setup promise is "paste
one API key", so these thin services talk to the Gemini Developer API via
the google-genai SDK instead. The registry checks GEMINI_API_KEY before
importing this module (M3 error policy) — imports here may assume pipecat
and google-genai are installed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from openai.types.audio import Transcription
from pipecat.frames.frames import ErrorFrame, Frame
from pipecat.services.tts_service import TTSService
from pipecat.services.whisper.base_stt import BaseWhisperSTTService

from google import genai
from google.genai import types as genai_types

GEMINI_TTS_SAMPLE_RATE = 24000  # Gemini TTS always outputs 24 kHz 16-bit mono PCM

_TRANSCRIBE_PROMPT = (
    "Transcribe this audio verbatim. Reply with only the transcription text — "
    "no preamble, no punctuation commentary."
)


class GeminiSTTService(BaseWhisperSTTService):
    """VAD-segmented STT: each speech segment is one generate_content call.

    Subclasses BaseWhisperSTTService purely for its segment handling, metrics,
    and TranscriptionFrame plumbing; the OpenAI client it builds internally is
    never used (_transcribe is fully overridden).
    """

    def __init__(self, *, api_key: str, model: str = "gemini-2.5-flash", **kwargs):
        super().__init__(model=model, api_key="unused", **kwargs)
        self._gemini = genai.Client(api_key=api_key)
        self._gemini_model = model

    async def _transcribe(self, audio: bytes) -> Transcription:
        response = await self._gemini.aio.models.generate_content(
            model=self._gemini_model,
            contents=[
                genai_types.Part.from_bytes(data=audio, mime_type="audio/wav"),
                _TRANSCRIBE_PROMPT,
            ],
        )
        return Transcription(text=(response.text or "").strip())
```

Then in `pyrrhon/voice/providers.py`: change `STT_PROVIDERS = ("groq", "openai", "gemini", "deepgram", "whisper-local")` and add the branch (after `openai`, before `deepgram`):

```python
    if provider == "gemini":
        key = _key("GEMINI_API_KEY", "Gemini STT")
        try:
            from pyrrhon.voice.gemini import GeminiSTTService
        except ImportError as exc:
            raise VoiceUnavailableError(
                f"Voice dependency missing ({exc}). Run: uv add google-genai "
                "— staying in text mode."
            ) from exc
        return GeminiSTTService(api_key=key, model=voice.stt_model or "gemini-2.5-flash")
```

Note for the implementer: if `BaseWhisperSTTService.__init__` in the installed pipecat rejects any of these kwargs, mirror how `pipecat/services/groq/stt.py` calls it — that file is the working reference.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_gemini_voice.py tests/test_voice_providers.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/voice/gemini.py pyrrhon/voice/providers.py tests/ pyproject.toml uv.lock
git commit -m "feat(voice): Gemini STT via plain API key (google-genai)"
```

---

### Task 5: Gemini TTS via API key

**Files:**
- Modify: `pyrrhon/voice/gemini.py` (add `GeminiTTSService`)
- Modify: `pyrrhon/voice/providers.py` (`TTS_PROVIDERS`, new branch in `create_tts`)
- Test: `tests/test_gemini_voice.py`, `tests/test_voice_providers.py`

**Interfaces:**
- Consumes: `pipecat.services.tts_service.TTSService` — `run_tts(text, context_id)` yields frames; the inherited helper `self._stream_audio_frames_from_iterator(async_iter_of_pcm_bytes, in_sample_rate=..., context_id=...)` handles resampling and frame framing (used the same way by `PiperTTSService` in the installed pipecat).
- Produces: `GeminiTTSService(api_key: str, voice: str = "Kore", model: str = "gemini-2.5-flash-preview-tts")`; `"gemini"` in `TTS_PROVIDERS`. Defaults the wizard (Task 8) shows: voice `Kore`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_gemini_voice.py`:

```python
async def test_tts_requests_audio_with_the_configured_voice(fake_genai):
    from pyrrhon.voice import gemini as gem

    pcm = b"\x00\x01" * 480
    part = types.SimpleNamespace(inline_data=types.SimpleNamespace(data=pcm))
    response = types.SimpleNamespace(
        candidates=[types.SimpleNamespace(
            content=types.SimpleNamespace(parts=[part]))]
    )
    fake_genai.aio.models.generate_content = AsyncMock(return_value=response)

    service = gem.GeminiTTSService.__new__(gem.GeminiTTSService)  # skip pipecat init
    service._client = fake_genai
    service._model = "gemini-2.5-flash-preview-tts"
    service._voice = "Puck"

    chunks = [c async for c in service._synthesize("hello")]
    assert chunks == [pcm]
    config = fake_genai.aio.models.generate_content.call_args.kwargs["config"]
    assert config.kwargs["response_modalities"] == ["AUDIO"]
```

And to `tests/test_voice_providers.py`:

```python
def test_gemini_tts_requires_key_then_builds_with_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(VoiceUnavailableError) as exc:
        create_tts(VoiceSettings(tts_provider="gemini"))
    assert "GEMINI_API_KEY" in str(exc.value)

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    captured: dict = {}
    _install_fake(monkeypatch, "pyrrhon.voice.gemini", "GeminiTTSService", captured)
    create_tts(VoiceSettings(tts_provider="gemini"))
    assert captured == {
        "api_key": "k",
        "voice": "Kore",
        "model": "gemini-2.5-flash-preview-tts",
    }
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_gemini_voice.py tests/test_voice_providers.py -k gemini -v`
Expected: new tests FAIL (no `GeminiTTSService`, unknown provider).

- [ ] **Step 3: Implement.** Append to `pyrrhon/voice/gemini.py`:

```python
class GeminiTTSService(TTSService):
    """Non-streaming Gemini TTS: one generate_content call per sentence chunk.

    Latency note: first-audio is one full API round-trip (~1s) — fine for
    Pyrrhon's sentence-chunked speech, slower than Cartesia/ElevenLabs
    streaming. Voices: Kore, Puck, Charon, Aoede, ... (Gemini prebuilt set).
    """

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "Kore",
        model: str = "gemini-2.5-flash-preview-tts",
        **kwargs,
    ):
        super().__init__(push_start_frame=True, push_stop_frames=True, **kwargs)
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._voice = voice

    async def _synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """One PCM chunk per call — split out so tests can drive it directly."""
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=text,
            config=genai_types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=genai_types.SpeechConfig(
                    voice_config=genai_types.VoiceConfig(
                        prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                            voice_name=self._voice
                        )
                    )
                ),
            ),
        )
        yield response.candidates[0].content.parts[0].inline_data.data

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        try:
            await self.start_tts_usage_metrics(text)
            async for frame in self._stream_audio_frames_from_iterator(
                self._synthesize(text),
                in_sample_rate=GEMINI_TTS_SAMPLE_RATE,
                context_id=context_id,
            ):
                await self.stop_ttfb_metrics()
                yield frame
        except Exception as exc:
            yield ErrorFrame(error=f"Gemini TTS failed: {exc}")
        finally:
            await self.stop_ttfb_metrics()
```

Implementer note: if `TTSService.__init__` in the installed pipecat requires a `settings=` dataclass, mirror `PiperTTSService.__init__` in `pipecat/services/piper/tts.py` (build a `Settings(model=..., voice=...)` and pass it through) — that file is the working reference.

Then in `pyrrhon/voice/providers.py`: `TTS_PROVIDERS = ("openai", "gemini", "cartesia", "elevenlabs", "deepgram", "piper")` and add the branch (after `openai`):

```python
    if provider == "gemini":
        key = _key("GEMINI_API_KEY", "Gemini TTS")
        try:
            from pyrrhon.voice.gemini import GeminiTTSService
        except ImportError as exc:
            raise VoiceUnavailableError(
                f"Voice dependency missing ({exc}). Run: uv add google-genai "
                "— staying in text mode."
            ) from exc
        return GeminiTTSService(
            api_key=key,
            voice=voice.tts_voice or "Kore",
            model=voice.tts_model or "gemini-2.5-flash-preview-tts",
        )
```

Also update the module docstring's provider lists in `pyrrhon/voice/providers.py` and the `VoiceSettings` docstring if not already done in Task 2.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_gemini_voice.py tests/test_voice_providers.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/voice/gemini.py pyrrhon/voice/providers.py tests/
git commit -m "feat(voice): Gemini TTS via plain API key"
```

---

### Task 6: Credentials store — `~/.pyrrhon/credentials.toml`

Keys entered in the wizard need a home that is (a) not the repo, (b) not `config.toml` (which users share/commit), (c) permission-restricted. A flat TOML file under `~/.pyrrhon/` with `0600` perms, loaded into the environment at startup with env-always-wins semantics. This is the same approach OpenClaw-class CLIs use; an OS keyring was considered and rejected (extra dependency, poor Windows/headless story).

**Files:**
- Create: `pyrrhon/config/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (Tasks 8, 9, 11 rely on these exact signatures):
  - `credentials_path(home: Path | None = None) -> Path`
  - `read_credentials(home: Path | None = None) -> dict[str, str]` — pure read, `{}` if missing.
  - `save_credentials(updates: dict[str, str], home: Path | None = None) -> Path` — merge-write, chmod 600.
  - `load_credentials(home: Path | None = None) -> dict[str, str]` — read + `os.environ.setdefault` each entry.

- [ ] **Step 1: Write the failing tests** — create `tests/test_credentials.py`:

```python
"""Credentials store: 0600 file, env-always-wins, values never in config.toml."""

import os
import stat

from pyrrhon.config.credentials import (
    credentials_path,
    load_credentials,
    read_credentials,
    save_credentials,
)


def test_roundtrip_and_merge(tmp_path):
    save_credentials({"GROQ_API_KEY": "gsk_1"}, home=tmp_path)
    save_credentials({"GEMINI_API_KEY": "AIza_2"}, home=tmp_path)
    assert read_credentials(home=tmp_path) == {
        "GROQ_API_KEY": "gsk_1",
        "GEMINI_API_KEY": "AIza_2",
    }


def test_values_with_quotes_and_backslashes_survive(tmp_path):
    tricky = 'ab"c\\d'
    save_credentials({"X_KEY": tricky}, home=tmp_path)
    assert read_credentials(home=tmp_path)["X_KEY"] == tricky


def test_missing_file_reads_empty(tmp_path):
    assert read_credentials(home=tmp_path) == {}


def test_load_sets_env_but_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    save_credentials(
        {"GROQ_API_KEY": "from-file", "GEMINI_API_KEY": "gem-file"}, home=tmp_path
    )
    load_credentials(home=tmp_path)
    assert os.environ["GROQ_API_KEY"] == "from-env"      # env always wins
    assert os.environ["GEMINI_API_KEY"] == "gem-file"    # file fills the gap


def test_file_is_owner_only_on_posix(tmp_path):
    save_credentials({"GROQ_API_KEY": "k"}, home=tmp_path)
    if os.name != "posix":
        return  # chmod is best-effort on Windows; nothing to assert
    mode = stat.S_IMODE(credentials_path(home=tmp_path).stat().st_mode)
    assert mode == 0o600
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — create `pyrrhon/config/credentials.py`:

```python
"""API-key storage: ~/.pyrrhon/credentials.toml, owner-only, env-always-wins.

Keys live here and ONLY here — never in config.toml (which users share and
commit) and never in logs. The file is chmod 0600 (best-effort on Windows,
where the user profile directory is already per-user). Environment variables
always take precedence: load_credentials uses os.environ.setdefault, so an
exported key beats a stored one.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path


def credentials_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".pyrrhon" / "credentials.toml"


def read_credentials(home: Path | None = None) -> dict[str, str]:
    path = credentials_path(home)
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {k: v for k, v in data.get("keys", {}).items() if isinstance(v, str)}


def save_credentials(updates: dict[str, str], home: Path | None = None) -> Path:
    path = credentials_path(home)
    merged = {**read_credentials(home), **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    # json.dumps produces a valid TOML basic string (quotes + escapes handled).
    lines = ["# Pyrrhon API keys — managed by `pyrrhon --setup`. Env vars win.", "[keys]"]
    lines += [f"{name} = {json.dumps(value)}" for name, value in sorted(merged.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows: chmod is limited; the profile dir is already per-user
    return path


def load_credentials(home: Path | None = None) -> dict[str, str]:
    stored = read_credentials(home)
    for name, value in stored.items():
        os.environ.setdefault(name, value)
    return stored
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_credentials.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/config/credentials.py tests/test_credentials.py
git commit -m "feat(config): owner-only credentials store with env-always-wins loading"
```

---

### Task 7: Provider catalog — the wizard's menu data

One authoritative list of every provider choice per task (LLM/STT/TTS): id, human label, key env var (or keyless), default model/voice, one-line note. The wizard and `/settings` render it; sync tests pin it to the real registries so a provider added to code can't be forgotten here (and vice versa).

**Files:**
- Create: `pyrrhon/config/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `BUILTIN_PROVIDERS` (settings.py), `STT_PROVIDERS`, `TTS_PROVIDERS` (voice/providers.py).
- Produces (Tasks 8 and 11 rely on these):
  - `ProviderChoice` frozen dataclass: `id: str`, `label: str`, `key_env: str | None`, `default_model: str | None`, `note: str = ""`
  - `LLM_CHOICES: tuple[ProviderChoice, ...]`, `STT_CHOICES: tuple[ProviderChoice, ...]`, `TTS_CHOICES: tuple[ProviderChoice, ...]` (for TTS, `default_model` carries the default *voice*).

- [ ] **Step 1: Write the failing tests** — create `tests/test_catalog.py`:

```python
"""The catalog is the wizard's menu — it must stay in sync with the registries."""

from pyrrhon.config.catalog import LLM_CHOICES, STT_CHOICES, TTS_CHOICES
from pyrrhon.config.settings import BUILTIN_PROVIDERS
from pyrrhon.voice.providers import STT_PROVIDERS, TTS_PROVIDERS


def test_every_llm_choice_is_a_builtin_provider():
    assert {c.id for c in LLM_CHOICES} == set(BUILTIN_PROVIDERS)


def test_voice_choices_match_the_registries_exactly():
    assert {c.id for c in STT_CHOICES} == set(STT_PROVIDERS)
    assert {c.id for c in TTS_CHOICES} == set(TTS_PROVIDERS)


def test_keyless_choices_are_marked_keyless():
    keyless = {c.id for c in LLM_CHOICES if c.key_env is None}
    assert keyless == {"ollama", "lmstudio"}
    assert {c.id for c in STT_CHOICES if c.key_env is None} == {"whisper-local"}
    assert {c.id for c in TTS_CHOICES if c.key_env is None} == {"piper"}


def test_every_choice_has_a_label_and_note():
    for choice in (*LLM_CHOICES, *STT_CHOICES, *TTS_CHOICES):
        assert choice.label
        assert choice.note
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — create `pyrrhon/config/catalog.py`:

```python
"""Every provider a user can pick, as data — the wizard and /settings render this.

Sync rule (pinned by tests/test_catalog.py): LLM ids mirror
BUILTIN_PROVIDERS; STT/TTS ids mirror the voice registry tuples. For TTS
choices, default_model carries the default VOICE (the registry's own
per-provider fallback), since a voice is the thing users actually pick.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderChoice:
    id: str
    label: str
    key_env: str | None          # None: keyless (local server / on-device model)
    default_model: str | None    # LLM/STT: model id; TTS: default voice
    note: str = ""


LLM_CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("groq", "Groq", "GROQ_API_KEY", "llama-3.3-70b-versatile",
                   "fast open-weights inference; generous free tier"),
    ProviderChoice("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4o-mini",
                   "GPT models"),
    ProviderChoice("gemini", "Google Gemini", "GEMINI_API_KEY", "gemini-2.5-flash",
                   "gemini-2.5-pro for the deep slot"),
    ProviderChoice("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek-chat",
                   "deepseek-reasoner for deep reasoning"),
    ProviderChoice("cerebras", "Cerebras", "CEREBRAS_API_KEY", "llama-3.3-70b",
                   "fastest tokens/sec around"),
    ProviderChoice("openrouter", "OpenRouter", "OPENROUTER_API_KEY",
                   "deepseek/deepseek-chat", "one key, many models"),
    ProviderChoice("huggingface", "Hugging Face", "HF_TOKEN",
                   "meta-llama/Llama-3.3-70B-Instruct",
                   "HF Inference Providers router"),
    ProviderChoice("ollama", "Ollama (local)", None, "llama3.2",
                   "runs on your machine — `ollama pull <model>` first"),
    ProviderChoice("lmstudio", "LM Studio (local)", None, "local-model",
                   "uses whatever model LM Studio has loaded"),
)

STT_CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("groq", "Groq Whisper", "GROQ_API_KEY", "whisper-large-v3-turbo",
                   "fast hosted Whisper"),
    ProviderChoice("openai", "OpenAI", "OPENAI_API_KEY", None,
                   "hosted transcription"),
    ProviderChoice("gemini", "Google Gemini", "GEMINI_API_KEY", "gemini-2.5-flash",
                   "transcription via the Gemini API"),
    ProviderChoice("deepgram", "Deepgram", "DEEPGRAM_API_KEY", None,
                   "streaming STT"),
    ProviderChoice("whisper-local", "Whisper (local)", None, None,
                   "on-device: tiny|base|small|medium|large-v3 or an HF id"),
)

TTS_CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("openai", "OpenAI", "OPENAI_API_KEY", "nova",
                   "no extra key if you already use OpenAI"),
    ProviderChoice("gemini", "Google Gemini", "GEMINI_API_KEY", "Kore",
                   "Gemini TTS voices: Kore, Puck, Charon, ..."),
    ProviderChoice("cartesia", "Cartesia", "CARTESIA_API_KEY", None,
                   "lowest latency; needs a voice id from your account"),
    ProviderChoice("elevenlabs", "ElevenLabs", "ELEVENLABS_API_KEY", None,
                   "needs a voice id from your account"),
    ProviderChoice("deepgram", "Deepgram Aura", "DEEPGRAM_API_KEY",
                   "aura-2-thalia-en", "low-latency hosted voices"),
    ProviderChoice("piper", "Piper (local)", None, "en_US-lessac-medium",
                   "free, on-device, no key and no server"),
)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/config/catalog.py tests/test_catalog.py
git commit -m "feat(config): provider catalog — single source for wizard and /settings menus"
```

---

### Task 8: The setup wizard

Navigable, numbered-menu wizard in the plain terminal (runs before any channel starts, like the plugin-consent prompt — no Textual dependency, works over SSH). Number picks, Enter accepts the default, `b` goes back a section, Ctrl-C aborts writing nothing. Keys via `getpass` (hidden). Ends with a summary + confirm; `n` restarts the flow. Writes `[fast]` and `[voice]` into `~/.pyrrhon/config.toml` (merge — other sections like `[mcp_servers]` survive; TOML comments do not, which the file header states) and keys via `save_credentials`.

**Files:**
- Create: `pyrrhon/config/wizard.py`
- Test: `tests/test_wizard.py`
- Modify: `pyproject.toml` (add `tomli-w`)

**Interfaces:**
- Consumes: `LLM_CHOICES`/`STT_CHOICES`/`TTS_CHOICES`/`ProviderChoice` (Task 7), `save_credentials`/`read_credentials`/`load_credentials` (Task 6).
- Produces (Task 9 relies on these):
  - `run_wizard(home: Path | None = None, console: Console | None = None, input_fn=None, getpass_fn=None) -> str` — returns a one-line summary.
  - `needs_setup(home: Path | None = None) -> bool` — True when `~/.pyrrhon/config.toml` is absent AND `GROQ_API_KEY` (the default provider's key) is unset.

- [ ] **Step 1: Add the dependency**

Run: `uv add tomli-w`

- [ ] **Step 2: Write the failing tests** — create `tests/test_wizard.py`:

```python
"""Setup wizard: scripted IO, no real terminal, keys never land in config.toml."""

import tomllib

from pyrrhon.config.credentials import read_credentials
from pyrrhon.config.wizard import needs_setup, run_wizard


def scripted(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


class QuietConsole:
    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))


def test_full_run_writes_config_and_credentials(tmp_path):
    console = QuietConsole()
    run_wizard(
        home=tmp_path,
        console=console,
        # LLM: pick 3 (gemini), accept default model, then voice: yes,
        # STT: pick 3 (gemini), TTS: pick 2 (gemini), confirm summary.
        input_fn=scripted("3", "", "y", "3", "2", "y"),
        getpass_fn=scripted("AIza-secret"),
    )
    config = tomllib.loads((tmp_path / ".pyrrhon" / "config.toml").read_text())
    assert config["fast"] == {"provider": "gemini", "model": "gemini-2.5-flash"}
    assert config["voice"]["stt_provider"] == "gemini"
    assert config["voice"]["tts_provider"] == "gemini"
    assert read_credentials(home=tmp_path) == {"GEMINI_API_KEY": "AIza-secret"}
    # The key value must never be echoed anywhere.
    assert not any("AIza-secret" in line for line in console.lines)


def test_skipping_voice_leaves_voice_section_alone(tmp_path):
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        input_fn=scripted("1", "", "n", "y"),   # groq, default model, no voice, confirm
        getpass_fn=scripted("gsk-abc"),
    )
    config = tomllib.loads((tmp_path / ".pyrrhon" / "config.toml").read_text())
    assert config["fast"]["provider"] == "groq"
    assert "voice" not in config


def test_existing_sections_survive_a_rerun(tmp_path):
    pyrrhon_dir = tmp_path / ".pyrrhon"
    pyrrhon_dir.mkdir()
    (pyrrhon_dir / "config.toml").write_text(
        '[mcp_servers.docs]\nurl = "http://localhost:9000"\n', encoding="utf-8"
    )
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        input_fn=scripted("1", "", "n", "y"),
        getpass_fn=scripted("gsk-abc"),
    )
    config = tomllib.loads((pyrrhon_dir / "config.toml").read_text())
    assert config["mcp_servers"]["docs"]["url"] == "http://localhost:9000"
    assert config["fast"]["provider"] == "groq"


def test_keyless_provider_asks_for_no_key(tmp_path):
    run_wizard(
        home=tmp_path,
        console=QuietConsole(),
        input_fn=scripted("8", "", "n", "y"),   # ollama, default model, no voice, confirm
        getpass_fn=scripted(),                  # would raise StopIteration if called
    )
    assert read_credentials(home=tmp_path) == {}


def test_needs_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert needs_setup(home=tmp_path) is True
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert needs_setup(home=tmp_path) is False
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    (tmp_path / ".pyrrhon").mkdir()
    (tmp_path / ".pyrrhon" / "config.toml").write_text("", encoding="utf-8")
    assert needs_setup(home=tmp_path) is False
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_wizard.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement** — create `pyrrhon/config/wizard.py`:

```python
"""First-run setup wizard: pick providers, paste keys, stored safely.

Runs in the plain terminal BEFORE any channel starts (same slot as the
plugin-consent prompt), so it works over SSH and never fights Textual for
the screen. Navigation: a number picks, Enter accepts the default, 'b'
steps back a section, Ctrl-C aborts without writing. Keys are read with
getpass (never echoed) and stored via pyrrhon.config.credentials — never
in config.toml. Rerunning is safe: config.toml is merged (other sections
survive; hand-written comments do not — the file header says so).
"""

from __future__ import annotations

import getpass
import os
import tomllib
from pathlib import Path

import tomli_w
from rich.console import Console

from pyrrhon.config.catalog import LLM_CHOICES, STT_CHOICES, TTS_CHOICES, ProviderChoice
from pyrrhon.config.credentials import read_credentials, save_credentials


class _Back(Exception):
    """User typed 'b' — step back one section."""


def needs_setup(home: Path | None = None) -> bool:
    """First run = no global config AND no key for the default provider."""
    home = home or Path.home()
    if (home / ".pyrrhon" / "config.toml").is_file():
        return False
    return not os.environ.get("GROQ_API_KEY")


def _key_status(choice: ProviderChoice, stored: dict[str, str]) -> str:
    if choice.key_env is None:
        return "no key needed"
    if os.environ.get(choice.key_env):
        return f"{choice.key_env} found in env"
    if choice.key_env in stored:
        return f"{choice.key_env} stored"
    return f"needs {choice.key_env}"


def _choose(console, ask, title: str, choices: tuple[ProviderChoice, ...],
            stored: dict[str, str], allow_back: bool) -> ProviderChoice:
    console.print(f"\n[bold]{title}[/bold]")
    for n, c in enumerate(choices, 1):
        console.print(f"  {n}. {c.label:<22} {c.note}  [{_key_status(c, stored)}]")
    hint = "number, Enter = 1" + (", b = back" if allow_back else "")
    while True:
        raw = ask(f"> pick ({hint}): ").strip().lower()
        if raw == "b" and allow_back:
            raise _Back
        if raw == "":
            return choices[0]
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        console.print("[yellow]Not an option — try again.[/yellow]")


def _ask_model(ask, choice: ProviderChoice, what: str) -> str | None:
    default = choice.default_model
    raw = ask(f"> {what} (Enter = {default or 'provider default'}): ").strip()
    return raw or default


def _collect_key(console, secret, choice: ProviderChoice,
                 keys: dict[str, str], stored: dict[str, str]) -> None:
    if choice.key_env is None or choice.key_env in keys:
        return
    if os.environ.get(choice.key_env) or choice.key_env in stored:
        console.print(f"{choice.key_env} already available — Enter keeps it.")
    value = secret(f"> {choice.key_env} (input hidden, Enter to skip): ").strip()
    if value:
        keys[choice.key_env] = value
    elif not (os.environ.get(choice.key_env) or choice.key_env in stored):
        console.print(
            f"[yellow]No {choice.key_env} given — Pyrrhon will ask again via "
            "`pyrrhon --setup`, or export it yourself.[/yellow]"
        )


def run_wizard(home: Path | None = None, console: Console | None = None,
               input_fn=None, getpass_fn=None) -> str:
    home = home or Path.home()
    console = console or Console()
    ask = input_fn or input
    secret = getpass_fn or getpass.getpass
    stored = read_credentials(home)

    state: dict = {"keys": {}}

    def _llm_section() -> None:
        choice = _choose(console, ask, "Which model provider should Pyrrhon think with?",
                         LLM_CHOICES, stored, allow_back=False)
        state["llm"] = choice
        state["llm_model"] = _ask_model(ask, choice, "model id")
        _collect_key(console, secret, choice, state["keys"], stored)

    def _voice_section() -> None:
        answer = ask("> configure voice (speech in/out)? [y/N]: ").strip().lower()
        if answer == "b":
            raise _Back
        state["voice_on"] = answer in ("y", "yes")
        if not state["voice_on"]:
            state.pop("stt", None)
            state.pop("tts", None)
            return
        stt = _choose(console, ask, "Speech-to-text (your voice → text):",
                      STT_CHOICES, stored, allow_back=True)
        state["stt"] = stt
        _collect_key(console, secret, stt, state["keys"], stored)
        tts = _choose(console, ask, "Text-to-speech (Pyrrhon's voice):",
                      TTS_CHOICES, stored, allow_back=True)
        state["tts"] = tts
        _collect_key(console, secret, tts, state["keys"], stored)

    sections = [_llm_section, _voice_section]
    index = 0
    while index < len(sections):
        try:
            sections[index]()
            index += 1
        except _Back:
            index = max(0, index - 1)

    summary = f"LLM: {state['llm'].id}/{state['llm_model']}"
    if state.get("voice_on"):
        summary += f" · STT: {state['stt'].id} · TTS: {state['tts'].id}"
    console.print(f"\n{summary}")
    if ask("> save this setup? [Y/n]: ").strip().lower() in ("n", "no"):
        return run_wizard(home=home, console=console,
                          input_fn=input_fn, getpass_fn=getpass_fn)

    _write_config(home, state)
    if state["keys"]:
        save_credentials(state["keys"], home=home)
    console.print("[green]Saved.[/green] Keys: ~/.pyrrhon/credentials.toml "
                  "(owner-only) · config: ~/.pyrrhon/config.toml")
    return summary


def _write_config(home: Path, state: dict) -> None:
    path = home / ".pyrrhon" / "config.toml"
    existing: dict = {}
    if path.is_file():
        with path.open("rb") as f:
            existing = tomllib.load(f)
    existing["fast"] = {"provider": state["llm"].id, "model": state["llm_model"]}
    if state.get("voice_on"):
        voice = existing.get("voice", {})
        voice["stt_provider"] = state["stt"].id
        voice["tts_provider"] = state["tts"].id
        # Only pin models/voices the catalog actually defaults — otherwise let
        # the registry's per-provider fallback (Task 2) apply.
        if state["stt"].default_model:
            voice["stt_model"] = state["stt"].default_model
        if state["tts"].default_model:
            voice["tts_voice"] = state["tts"].default_model
        existing["voice"] = voice
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"# Managed by `pyrrhon --setup` (rerunning merges sections,"
                b" comments are not preserved).\n")
        tomli_w.dump(existing, f)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_wizard.py -v`
Expected: all PASS. If an index in the scripted answers doesn't line up with the catalog order, fix the *test's* pick numbers against `LLM_CHOICES` order (gemini is 3rd, ollama 8th) — not the catalog order.

- [ ] **Step 6: Commit**

```bash
git add pyrrhon/config/wizard.py tests/test_wizard.py pyproject.toml uv.lock
git commit -m "feat(setup): navigable first-run wizard — provider selection and safe key storage"
```

---

### Task 9: Wire the wizard into startup — `--setup`, first-run detection, credential loading

**Files:**
- Modify: `pyrrhon/cli.py`
- Modify: `pyrrhon/config/wizard.py` (add `ensure_configured`)
- Modify: `pyrrhon/repl.py:193` (`run_repl`), `pyrrhon/tui/app.py:208` (`run_tui`)
- Test: `tests/test_cli.py`, `tests/test_wizard.py`

**Interfaces:**
- Consumes: `run_wizard`, `needs_setup` (Task 8), `load_credentials` (Task 6).
- Produces: `ensure_configured(home: Path | None = None, ask=None) -> None` in `pyrrhon/config/wizard.py` — loads stored credentials into env, then offers the wizard interactively on first run. Both channels call it first thing.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_wizard.py`:

```python
def test_ensure_configured_loads_credentials_and_skips_wizard_when_configured(
    tmp_path, monkeypatch
):
    from pyrrhon.config.credentials import save_credentials
    from pyrrhon.config.wizard import ensure_configured

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (tmp_path / ".pyrrhon").mkdir()
    (tmp_path / ".pyrrhon" / "config.toml").write_text("", encoding="utf-8")
    save_credentials({"GEMINI_API_KEY": "gem"}, home=tmp_path)

    ensure_configured(home=tmp_path, ask=lambda prompt: (_ for _ in ()).throw(
        AssertionError("wizard offered despite existing config")))
    import os
    assert os.environ["GEMINI_API_KEY"] == "gem"


def test_ensure_configured_offers_wizard_on_first_run(tmp_path, monkeypatch):
    from pyrrhon.config import wizard as wizard_mod

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ran = {}
    monkeypatch.setattr(wizard_mod, "run_wizard", lambda home: ran.setdefault("home", home))
    wizard_mod.ensure_configured(home=tmp_path, ask=lambda prompt: "y")
    assert ran["home"] == tmp_path


def test_ensure_configured_respects_a_no(tmp_path, monkeypatch):
    from pyrrhon.config import wizard as wizard_mod

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(wizard_mod, "run_wizard",
                        lambda home: (_ for _ in ()).throw(AssertionError("ran anyway")))
    wizard_mod.ensure_configured(home=tmp_path, ask=lambda prompt: "n")
```

Append to `tests/test_cli.py` (match its existing monkeypatch style for channel entry points):

```python
def test_setup_flag_runs_the_wizard_then_launches(monkeypatch):
    calls = []
    monkeypatch.setattr("pyrrhon.config.wizard.run_wizard", lambda: calls.append("wizard") or "ok")
    monkeypatch.setattr("pyrrhon.tui.app.run_tui", lambda repo, voice=False: calls.append("tui"))
    from pyrrhon.cli import main

    main(["--setup"])
    assert calls == ["wizard", "tui"]
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_wizard.py tests/test_cli.py -v`
Expected: new tests FAIL (`ensure_configured` missing, `--setup` unknown flag).

- [ ] **Step 3: Implement.** Append to `pyrrhon/config/wizard.py`:

```python
def ensure_configured(home: Path | None = None, ask=None) -> None:
    """Channel startup hook: stored keys → env; offer the wizard on first run.

    Runs before the event loop exists (plain input is fine — same stage as
    the plugin-consent prompt). Declining is remembered only for this
    process: next launch offers again until a config exists or a key is set.
    """
    from pyrrhon.config.credentials import load_credentials

    load_credentials(home)
    if not needs_setup(home):
        return
    answer = (ask or input)(
        "No Pyrrhon configuration found. Run the setup wizard now? [Y/n] "
    ).strip().lower()
    if answer in ("", "y", "yes"):
        run_wizard(home=home)
        load_credentials(home)
```

In `pyrrhon/cli.py`, add the flag after `--voice`:

```python
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the provider/API-key setup wizard, then start",
    )
```

and after `args = parser.parse_args(argv)`:

```python
    if args.setup:
        from pyrrhon.config.wizard import run_wizard

        run_wizard()
```

In `pyrrhon/repl.py` `run_repl`, right after the `repo_root.is_dir()` check, and in `pyrrhon/tui/app.py` `run_tui` at the same spot:

```python
    from pyrrhon.config.wizard import ensure_configured

    ensure_configured()  # stored keys → env; first run offers the wizard
```

(Top-level import in each file is fine too — match the file's existing import style; `repl.py` imports at top, `tui/app.py` imports `load_channel_plugins` lazily.)

- [ ] **Step 4: Run the full suite** (startup paths changed)

Run: `uv run pytest`
Expected: all PASS. Watch for existing `test_init_and_repl.py`/`test_tui_app.py` tests that call `run_repl`/`run_tui` — if they now hit `ensure_configured`'s interactive prompt, those tests must monkeypatch `ensure_configured` to a no-op **or** set `GROQ_API_KEY` in their env setup; prefer setting the env var, it exercises the real code path.

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/cli.py pyrrhon/config/wizard.py pyrrhon/repl.py pyrrhon/tui/app.py tests/
git commit -m "feat(cli): --setup flag and first-run wizard offer; stored keys load at startup"
```

---

### Task 10: Branding — mascot banner in both channels

Pyrrhon is named for Pyrrho of Elis, the skeptic. The mascot is a small skeptical owl. Banner must stay under 60 columns (TUI transcript pane), ASCII-only (Windows terminals), and live in one module.

**Files:**
- Create: `pyrrhon/branding.py`
- Modify: `pyrrhon/repl.py` (`_repl_main` welcome), `pyrrhon/tui/app.py` (`on_mount` welcome)
- Test: `tests/test_branding.py`

**Interfaces:**
- Consumes: `pyrrhon.__version__`.
- Produces: `banner() -> str` (multi-line, includes name + version + tagline). Channels render it verbatim.

- [ ] **Step 1: Write the failing test** — create `tests/test_branding.py`:

```python
from pyrrhon import __version__
from pyrrhon.branding import banner


def test_banner_names_the_product_and_version():
    text = banner()
    assert "P Y R R H O N" in text
    assert __version__ in text
    assert all(len(line) <= 60 for line in text.splitlines())
    assert text.isascii()  # Windows terminals; TUI pane safety
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_branding.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — create `pyrrhon/branding.py`:

```python
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
```

In `pyrrhon/repl.py` `_repl_main`, replace the plain welcome print with:

```python
    from pyrrhon.branding import banner

    console.print(f"[bold cyan]{banner()}[/bold cyan]")
    console.print(
        f"Discussing [cyan]{repo_root.name}[/cyan]. Commands: /help, /quit"
    )
```

In `pyrrhon/tui/app.py` `on_mount`, replace the welcome `transcript.write(...)` with:

```python
        from pyrrhon.branding import banner

        transcript = self.query_one("#transcript", RichLog)
        transcript.write(Text(banner(), style="bold cyan"))
        transcript.write(
            f"Discussing {self.repo_root.name}. Type /help for commands."
        )
```

- [ ] **Step 4: Run branding + channel tests**

Run: `uv run pytest tests/test_branding.py tests/test_tui_app.py tests/test_init_and_repl.py -v`
Expected: all PASS (fix any welcome-text assertion in existing channel tests to match the new copy).

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/branding.py pyrrhon/repl.py pyrrhon/tui/app.py tests/
git commit -m "feat(branding): skeptical-owl banner in REPL and TUI"
```

---

### Task 11: `/settings` — show providers, models, and masked key status

**Files:**
- Create: `pyrrhon/commands/settings_cmd.py`
- Modify: `pyrrhon/repl.py:12` and `pyrrhon/tui/app.py:20` (add `settings_cmd` to the registering import)
- Test: `tests/test_settings_cmd.py`

**Interfaces:**
- Consumes: `load_settings` (settings.py), `read_credentials` (Task 6), all three `*_CHOICES` tuples (Task 7), `command`/`CommandContext` (registry).
- Produces: the `/settings` slash command. Key *values* never appear — only `env` / `stored` / `missing`.

- [ ] **Step 1: Write the failing test** — create `tests/test_settings_cmd.py`:

```python
"""/settings shows what's configured and where keys come from — never key values."""

from pathlib import Path

import pytest

from pyrrhon.commands import settings_cmd  # noqa: F401 — registers /settings
from pyrrhon.commands.registry import CommandContext, dispatch


class DummyUI:
    def notify(self, text: str) -> None: ...


@pytest.fixture
def ctx(tmp_path):
    return CommandContext(repo_root=tmp_path, agent=None, ui=DummyUI())


async def test_settings_lists_slots_and_key_status(ctx, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret_value")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Isolate from the developer's real ~/.pyrrhon/credentials.toml — a
    # stored key there would flip the "missing" assertion below.
    monkeypatch.setattr("pyrrhon.commands.settings_cmd.read_credentials", lambda: {})
    out = await dispatch("/settings", ctx)
    assert "groq" in out                      # default fast slot provider
    assert "GROQ_API_KEY" in out and "env" in out
    assert "GEMINI_API_KEY" in out and "missing" in out
    assert "gsk_secret_value" not in out      # value never rendered
    assert "--setup" in out                   # points at the wizard
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_settings_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — create `pyrrhon/commands/settings_cmd.py`:

```python
"""/settings — what Pyrrhon is configured to use, and where each key comes from."""

from __future__ import annotations

import os

from pyrrhon.commands.registry import CommandContext, command
from pyrrhon.config.catalog import LLM_CHOICES, STT_CHOICES, TTS_CHOICES
from pyrrhon.config.credentials import read_credentials
from pyrrhon.config.settings import load_settings


def _key_line(env: str, stored: dict[str, str]) -> str:
    if os.environ.get(env):
        return f"  {env}: env"
    if env in stored:
        return f"  {env}: stored (~/.pyrrhon/credentials.toml)"
    return f"  {env}: missing"


@command("settings", "Show providers, models, and API-key status")
def settings_command(args: str, ctx: CommandContext) -> str:
    settings = load_settings(ctx.repo_root)
    stored = read_credentials()
    deep = settings.deep_slot
    lines = [
        f"fast:  {settings.fast.provider}/{settings.fast.model}",
        f"deep:  {deep.provider}/{deep.model}"
        + ("" if settings.deep else "  (= fast; set [deep] to change)"),
        f"stt:   {settings.voice.stt_provider}"
        + (f" ({settings.voice.stt_model})" if settings.voice.stt_model else ""),
        f"tts:   {settings.voice.tts_provider}"
        + (f" ({settings.voice.tts_voice})" if settings.voice.tts_voice else ""),
        "keys:",
    ]
    envs = sorted({
        c.key_env
        for c in (*LLM_CHOICES, *STT_CHOICES, *TTS_CHOICES)
        if c.key_env is not None
    })
    lines += [_key_line(env, stored) for env in envs]
    lines.append("change any of this with: pyrrhon --setup")
    return "\n".join(lines)
```

Add `settings_cmd` to the registering import in `pyrrhon/repl.py:12` and `pyrrhon/tui/app.py:20`:

```python
from pyrrhon.commands import builtin, debug_cmd, mcp_cmd, mode_cmd, plugins_cmd, settings_cmd, voice_cmd  # noqa: F401 — registers commands
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_settings_cmd.py tests/test_command_registry.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pyrrhon/commands/settings_cmd.py pyrrhon/repl.py pyrrhon/tui/app.py tests/test_settings_cmd.py
git commit -m "feat(commands): /settings — providers, models, masked key status"
```

---

### Task 12: Safety lockdown — pin the no-dangerous-commands invariants with tests

The architecture is already safe: git tools are a fixed read-only allowlist (`log`/`blame`/`show` via `create_subprocess_exec`, argv lists, never `shell=True`), `write_spec` is the only repo-writing tool and only accepts six filenames under `docs/design/`, the deep subagent's belt is read-only, and repo-level plugin code needs one consent per repo. Nothing here is enforced by a test today — an innocent refactor could add a shell tool to the belt without anyone noticing. This task pins the invariants.

**Files:**
- Create: `tests/test_safety.py`
- Modify: `README.md` (add a "Security model" section)

**Interfaces:**
- Consumes: `build_agent` (repl.py), tool classes, `ThinkDeeperTool.tools` (dict, `pyrrhon/core/agent/escalate.py:60`), `SPEC_FILENAMES` (spec_writer.py).
- Produces: nothing new — a regression fence.

- [ ] **Step 1: Write the tests** — create `tests/test_safety.py`:

```python
"""Safety invariants: the agent cannot execute dangerous commands, by construction.

These tests are a fence, not a feature: they pin the properties that make it
safe to let a voice agent loose on a repo — a frozen tool belt, read-only git
subcommands behind argv-list subprocess calls, one write tool confined to six
filenames under docs/design/, and a read-only deep-subagent belt. If a change
breaks one of these, that change needs a design discussion, not a test edit.
"""

from pathlib import Path

import pytest

from pyrrhon.core.tools.git import GitBlameTool, GitLogTool, GitShowTool
from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES, WriteSpecTool
from pyrrhon.repl import build_agent
from tests.helpers import FakeLLM  # scripted-replies double, defined in tests/helpers.py

EXPECTED_BELT = {
    "read_file", "grep", "glob", "remember",
    "find_symbol", "find_references", "list_dependencies", "repo_map",
    "git_log", "git_blame", "git_show",
    "web_search", "web_fetch", "write_spec", "think_deeper",
}

READ_ONLY = EXPECTED_BELT - {"write_spec", "remember", "think_deeper"}


@pytest.fixture
def agent(tmp_path):
    # home=tmp_path: isolate from the developer's real ~/.pyrrhon/plugins —
    # a global plugin contributing tools would break the exact-belt assertion.
    return build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp_path)


def test_the_tool_belt_is_exactly_the_reviewed_set(agent):
    assert set(agent.tools) == EXPECTED_BELT


def test_deep_subagent_belt_is_read_only(agent):
    deep = agent.tools["think_deeper"]
    assert set(deep.tools) <= READ_ONLY


async def test_git_show_rejects_flag_injection(tmp_path):
    tool = GitShowTool(tmp_path)
    for evil in ("--output=/tmp/pwn", "-p", ""):
        assert "ERROR" in await tool.run(ref=evil)


async def test_git_tools_reject_paths_outside_the_repo(tmp_path):
    log = GitLogTool(tmp_path)
    assert "outside the repo" in await log.run(path="../../etc/passwd")
    blame = GitBlameTool(tmp_path)
    assert "outside the repo" in await blame.run(path="../secrets.txt")


async def test_write_spec_only_writes_the_six_artifacts(tmp_path):
    tool = WriteSpecTool(tmp_path)
    for evil in ("../../evil.md", "PRD.md/../../../evil.md", ".bashrc", "evil.md"):
        result = await tool.run(filename=evil, content="x")
        assert "ERROR" in result
    assert not (tmp_path.parent / "evil.md").exists()
    ok = await tool.run(filename="PRD.md", content="# ok")
    assert "PRD.md" in ok
    assert (tmp_path / "docs" / "design" / "PRD.md").read_text(encoding="utf-8") == "# ok"
    assert set(SPEC_FILENAMES) == {"PRD.md", "HLD.md", "LLD.md", "api.md", "database.md", "risks.md"}


def test_no_tool_shells_out_except_the_git_allowlist():
    """Grep-level fence: the only subprocess users in core tools are git.py
    (argv-list, allowlisted subcommands) and nothing else."""
    import pyrrhon.core.tools as tools_pkg

    offenders = []
    for path in Path(tools_pkg.__path__[0]).glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "subprocess" in text and path.name != "git.py":
            offenders.append(path.name)
        if "shell=True" in text:
            offenders.append(f"{path.name} (shell=True)")
    assert offenders == []
```

(Verified against the working tree: `tests/helpers.py` defines `FakeLLM(replies)` and existing tests call `build_agent(tmp_path, llm=FakeLLM([]), deep_llm=FakeLLM([]))` with no API key — see `tests/test_build_agent_m4.py:47` for the established pattern.)

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_safety.py -v`
Expected: all PASS immediately (this pins existing behavior). If any FAILS, that is a real finding — investigate the hole before touching the test, per superpowers:systematic-debugging.

- [ ] **Step 3: Add the "Security model" section to `README.md`** (after the feature overview):

```markdown
## Security model

Pyrrhon is built so a voice agent *cannot* damage your repo, even when the
model is wrong:

- **Read-only by construction.** The agent's only write tool is `write_spec`,
  which accepts exactly six filenames (`PRD.md`, `HLD.md`, `LLD.md`, `api.md`,
  `database.md`, `risks.md`) and only ever writes under `docs/design/`.
- **No shell.** Git access is three read-only subcommands (`log`, `blame`,
  `show`) executed as argv lists — never a shell, so there is nothing to
  inject. Paths are resolved and rejected if they escape the repo.
- **Grounded speech.** Every `file:line` claim is verified against the real
  repo before it is spoken; unverifiable claims are stripped (voice) or
  corrected once (screen). Pyrrhon says "I'm not certain" instead of guessing.
- **Keys stay out of the repo.** API keys live in `~/.pyrrhon/credentials.toml`
  (owner-only permissions), never in project config; environment variables
  always take precedence.
- **Consent for third-party code.** Repo-level plugin code runs only after an
  explicit one-time consent per repo; MCP servers run only if you configured
  them.

These invariants are pinned by `tests/test_safety.py`.
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_safety.py README.md
git commit -m "test(safety): pin read-only belt, git allowlist, and write_spec confinement"
```

---

### Task 13: `.env.example` and documentation

**Files:**
- Create: `.env.example`
- Modify: `README.md` (quickstart + provider matrix), `CLAUDE.md` (current-state paragraph)

**Interfaces:** none — documentation of everything above.

- [ ] **Step 1: Create `.env.example`:**

```bash
# Pyrrhon environment — copy to .env and fill in what you use, or just run
# `pyrrhon --setup` (stores keys in ~/.pyrrhon/credentials.toml instead).
# Env vars always win over stored credentials. NEVER commit a real .env.
# Note: Pyrrhon reads real environment variables; source this file from your
# shell (or use your IDE's env support) — it does not auto-load .env files.

# ── LLM providers (pick at least one; groq is the default fast slot) ──
GROQ_API_KEY=            # https://console.groq.com/keys
OPENAI_API_KEY=          # https://platform.openai.com/api-keys — also OpenAI STT/TTS
GEMINI_API_KEY=          # https://aistudio.google.com/apikey — also Gemini STT/TTS
DEEPSEEK_API_KEY=        # https://platform.deepseek.com/api_keys
CEREBRAS_API_KEY=        # https://cloud.cerebras.ai
OPENROUTER_API_KEY=      # https://openrouter.ai/keys
HF_TOKEN=                # https://huggingface.co/settings/tokens (Inference Providers)
# Keyless local LLMs need no entry here: ollama (http://localhost:11434/v1)
# and lmstudio (http://localhost:1234/v1) just need their server running.

# ── Voice-only providers (optional) ──
DEEPGRAM_API_KEY=        # https://console.deepgram.com — STT + Aura TTS
CARTESIA_API_KEY=        # https://play.cartesia.ai/keys — lowest-latency TTS
ELEVENLABS_API_KEY=      # https://elevenlabs.io — TTS
# Keyless local voice needs no entry: whisper-local (STT) and piper (TTS)
# run on-device.
```

- [ ] **Step 2: Update `README.md`.** Add/replace a quickstart and a provider matrix section:

```markdown
## Quickstart

    uv sync
    uv run pyrrhon --setup     # pick providers, paste keys (stored owner-only)
    uv run pyrrhon [repo]      # TUI; add --text for the REPL, --voice for voice

First launch without configuration offers the same wizard automatically.
`/settings` shows what's configured; `pyrrhon --setup` changes it.

## Providers

| Task | Cloud (API key) | Local (keyless) |
|---|---|---|
| LLM | Groq, OpenAI, Gemini, DeepSeek, Cerebras, OpenRouter, Hugging Face | Ollama, LM Studio |
| STT | Groq Whisper, OpenAI, Gemini, Deepgram | whisper-local (faster-whisper: tiny→large-v3, distil, any HF id) |
| TTS | OpenAI, Gemini, Cartesia, ElevenLabs, Deepgram Aura | Piper (in-process, auto-downloads voices) |

Any OpenAI-compatible endpoint works as a custom provider via
`[providers.<name>]` in `.pyrrhon.toml` (`base_url` + `api_key_env`).

**Why no Gemini Live speech-to-speech?** Gemini Live generates speech
directly from audio, which would bypass Pyrrhon's agent loop — and with it
the grounding gate that verifies every `file:line` claim before it is
spoken. Confident hallucination out loud is Pyrrhon's worst failure mode,
so Gemini participates as LLM/STT/TTS (each behind the gate) instead.
```

- [ ] **Step 3: Update `CLAUDE.md`** — in the "Current state" section, append:

```markdown
M9 (provider expansion + onboarding) — DeepSeek/Hugging Face LLM providers,
Gemini STT/TTS via plain API key (`pyrrhon/voice/gemini.py`), in-process
Piper, per-provider voice defaults, a first-run setup wizard
(`pyrrhon/config/wizard.py`, `pyrrhon --setup`) with owner-only key storage
(`pyrrhon/config/credentials.py`), `/settings`, the branding banner, and
safety-invariant tests (`tests/test_safety.py`). Gemini Live speech-to-speech
is parked: it would bypass the grounding gate.
```

- [ ] **Step 4: Verify docs don't break anything**

Run: `uv run pytest`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md CLAUDE.md
git commit -m "docs: .env.example, provider matrix, quickstart, Gemini Live rationale"
```

---

### Task 14: Final QA sweep

**Files:** none new — verification only (superpowers:verification-before-completion).

- [ ] **Step 1: Full suite from a clean sync**

Run: `uv sync && uv run pytest`
Expected: every test passes (≈259 pre-existing + ≈35 new). Paste the summary line as evidence.

- [ ] **Step 2: CLI smoke tests**

Run: `uv run pyrrhon --version`
Expected: `pyrrhon 0.1.0`

Run: `uv run pyrrhon --help`
Expected: shows `--text`, `--voice`, `--setup`.

- [ ] **Step 3: Wizard smoke test against a throwaway HOME** (PowerShell):

```powershell
$env:PYRRHON_SMOKE = "$env:TEMP\pyrrhon-smoke"; New-Item -ItemType Directory -Force $env:PYRRHON_SMOKE
uv run python -c "from pathlib import Path; from pyrrhon.config.wizard import run_wizard; answers = iter(['8', '', 'n', 'y']); print(run_wizard(home=Path(r'$env:TEMP\pyrrhon-smoke'), input_fn=lambda p='': next(answers), getpass_fn=lambda p='': ''))"
Get-Content "$env:TEMP\pyrrhon-smoke\.pyrrhon\config.toml"
```

Expected: summary line `LLM: ollama/llama3.2`; config.toml shows `[fast]` with ollama.

- [ ] **Step 4: Live-key smoke (only if a real key is available in the env)** — start `uv run pyrrhon --text .` and ask one question; confirm a grounded answer with a citation. If no key is available, state that this step was skipped and why.

- [ ] **Step 5: Self-review against this plan's gap-analysis table** — every ❌/⚠️ row has shipped or is explicitly parked. Then commit anything outstanding and stop.

```bash
git status   # expect: clean
```

---

## Plan Self-Review (already performed)

- **Spec coverage:** every item in the user's request maps to a task or an explicit park: DeepSeek/HF LLM → T1; local whisper models → T2; piper → T3; Gemini STT/TTS via API → T4/T5; Gemini Live → parked with rationale (T13); Cerebras/Ollama/LM Studio/HF-local → already built, exposed via wizard + docs; OpenClaw-style onboarding + safe key storage → T6–T9; safety/no-dangerous-commands → T12 (hallucination was already handled by the grounding gate — verified, nothing to add); logo/mascot + interactive polish → T10–T11; `.env.example` → T13.
- **Type consistency:** `ProviderChoice(id, label, key_env, default_model, note)` is used identically in T7/T8/T11; `read_credentials`/`save_credentials`/`load_credentials` signatures match across T6/T8/T9/T11; `VoiceSettings.stt_model/tts_voice: str | None` semantics from T2 are honored by T4/T5's `or`-defaults.
- **Known judgment calls the executor may hit:** exact pipecat base-class constructor kwargs (T4/T5 include the working in-tree reference file to mirror); wizard menu indices in tests are tied to catalog order (T8 notes which side to fix).
- **Review round 2 (2026-07-06, after external review):** verified against the working tree that `pipeline.py:55-56` already routes through `create_stt`/`create_tts` (no factory-disconnect refactor needed) and that `repl.py:83-84` registers `list_dependencies`/`repo_map` in the default belt (EXPECTED_BELT in T12 is correct as written — both were flagged from a stale snapshot). Hardened three tests against developer-machine state: T4's fixture evicts `pyrrhon.voice.gemini` from the module cache before faking `google.genai`; T11's test stubs `read_credentials` so real stored keys can't flip the "missing" assertion; T12's fixture passes `home=tmp_path` so real global plugins can't widen the belt.
