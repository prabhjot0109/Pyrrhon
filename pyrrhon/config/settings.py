"""Load and merge Pyrrhon settings: global ~/.pyrrhon/config.toml then <repo>/.pyrrhon.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrrhon.config.trust import Grant, TrustFile, digest_value, read_trust_file
from pyrrhon.core.providers.registry import LLM_PROVIDERS, find_llm


class ModelSlot(BaseModel):
    provider: str
    model: str


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str = ""  # empty: provider needs no key (local servers)


class MCPServerConfig(BaseModel):
    """One [mcp_servers.<name>] table: a stdio command OR a streamable-HTTP url."""

    command: str | None = None
    args: list[str] = []
    url: str | None = None

    @model_validator(mode="after")
    def _exactly_one_transport(self) -> "MCPServerConfig":
        if (self.command is None) == (self.url is None):
            raise ValueError(
                "an MCP server needs exactly one of 'command' (stdio) or "
                "'url' (streamable HTTP)"
            )
        return self


# Derived, not hand-listed: a provider cannot exist in the table and be missing
# here. See pyrrhon/core/providers/registry.py — importing it is safe from
# config/ because that module is pure stdlib data and imports nothing back.
BUILTIN_PROVIDERS: dict[str, ProviderConfig] = {
    provider.id: ProviderConfig(
        base_url=provider.base_url, api_key_env=provider.api_key_env
    )
    for provider in LLM_PROVIDERS
}


class VoiceSettings(BaseModel):
    """M3/M8/M9 voice-channel knobs (TOML section [voice]).

    stt_provider: groq | openai | gemini | deepgram | whisper-local
    tts_provider: openai | gemini | cartesia | elevenlabs | deepgram | piper
    stt_model / tts_voice are provider-specific; when unset each provider
    applies its own sensible default (see pyrrhon/voice/registry.py).
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
    # "smart": LocalSmartTurnAnalyzerV3 decides end-of-turn semantically.
    # "vad":   the old fixed-silence threshold. Kept as the documented
    #          fallback for constrained machines; never delete it.
    turn_detection: Literal["smart", "vad"] = "smart"
    # 0 disables. When set, the agent re-engages after this much user silence.
    idle_timeout_sec: float = 0.0
    noise_filter: bool = True                  # RNNoise on the mic
    metrics: bool = True                       # pipecat per-service latency observers


class ModelSettings(BaseModel):
    """Generation knobs sent with every completion (TOML section [model]).

    Both default to None, meaning "send nothing and take the provider's
    default" — so configuring neither leaves request payloads byte-identical
    to before this section existed.

    max_tokens is a runaway guard, not the way to keep voice turns short: a
    hard cap truncates mid-sentence, which sounds worse than a slightly long
    answer. VOICE_STYLE is what actually keeps spoken turns to a few
    sentences. Set this if you want a hard ceiling on cost or on a model that
    ignores the prompt and monologues.
    """

    max_tokens: int | None = None
    temperature: float | None = None


class ContextSettings(BaseModel):
    """Context-window budgeting (TOML section [context])."""

    # None means "learn it" (M16a). A flat number here was budgeting 90000
    # tokens whether the configured model had an 8k window or a 131k one, and
    # whether the account's per-minute allowance was 6k or 600k — while the
    # provider reported x-ratelimit-limit-tokens on every response. An explicit
    # value still wins: a number the user chose outranks a learned guess.
    budget_tokens: int | None = None  # estimated-token ceiling before compaction
    keep_last_messages: int = 8       # recent messages kept verbatim


class GroundingSettings(BaseModel):
    """Grounding strictness (TOML section [grounding]).

    require_provenance: a citation must point at a line some tool result in
    THIS turn actually displayed, not merely a line that exists. Defaults off
    until the M13 eval shows the pass rate holds — it is a real tightening, and
    a false downgrade makes Pyrrhon sound unsure about work it genuinely did.

    Unprivileged on purpose: a repo can only make this gate STRICTER or leave
    it as-is, and neither direction runs code, redirects a key, or writes the
    system prompt. See PRIVILEGED_PATHS below for what does.
    """

    require_provenance: bool = False


class TelemetrySettings(BaseModel):
    """OpenTelemetry export (TOML section [telemetry]).

    otlp_endpoint is PRIVILEGED: it names where traces — which carry
    transcripts, prompts, and tool output — are sent. A repo that could set it
    would have an exfiltration channel, exactly like voice.tts_url.
    """

    otel_enabled: bool = False
    otlp_endpoint: str | None = None
    service_name: str = "pyrrhon"


class Settings(BaseModel):
    fast: ModelSlot = ModelSlot(provider="groq", model="llama-3.3-70b-versatile")
    deep: ModelSlot | None = None
    vision: ModelSlot | None = None
    providers: dict[str, ProviderConfig] = {}
    voice: VoiceSettings = VoiceSettings()
    model: ModelSettings = ModelSettings()
    context: ContextSettings = ContextSettings()
    grounding: GroundingSettings = GroundingSettings()
    telemetry: TelemetrySettings = TelemetrySettings()
    mcp_servers: dict[str, MCPServerConfig] = {}
    # Slot name ("fast"/"deep") -> providers tried IN ORDER after the slot's
    # primary. Entry format: "provider" or "provider/model" (first '/' splits).
    fallbacks: dict[str, list[str]] = {}
    # Repo-supplied config that has NOT been granted. Never applied; carried
    # here so the channel can prompt for it exactly once. exclude=True keeps
    # consent decisions out of model_dump(), which is what /settings and the
    # wizard write back into config.toml.
    pending_grants: list[Grant] = Field(default_factory=list, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def deep_slot(self) -> ModelSlot:
        # Spec rule: the deep slot falls back to the fast slot when unset.
        return self.deep or self.fast

    def vision_slot(self) -> ModelSlot | None:
        """Which model answers questions about images, or None if none can.

        Explicit [vision] wins — whoever wrote it knows what their model can
        do. Otherwise the fast slot, but only if the table says that provider
        accepts image content: sending an image to a text-only endpoint
        produces a confusing 400 instead of a useful error, and a provider the
        table has never heard of (a user-declared [providers.x]) carries no
        claim either way, so it declines too.
        """
        if self.vision is not None:
            return self.vision
        provider = find_llm(self.fast.provider)
        return self.fast if provider is not None and provider.vision else None

    def provider_for(self, slot: ModelSlot) -> ProviderConfig:
        if slot.provider in self.providers:
            return self.providers[slot.provider]
        if slot.provider in BUILTIN_PROVIDERS:
            return BUILTIN_PROVIDERS[slot.provider]
        raise KeyError(
            f"Unknown provider '{slot.provider}'. Add [providers.{slot.provider}] "
            f"to .pyrrhon.toml or use one of: {', '.join(BUILTIN_PROVIDERS)}"
        )


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


# Repo-supplied keys that RUN something, REDIRECT where prompts and keys go, or
# WRITE the system prompt. Quarantined until granted. `voice.tts_url` is here
# despite the rest of [voice] being harmless: Piper HTTP mode POSTs the text
# Pyrrhon is about to speak to that URL (voice/factory.py), so a repo that
# sets it exfiltrates the conversation. The partition is therefore keyed on
# dotted paths, not on top-level table names — privilege does not line up with
# TOML's table boundaries and pretending it does is how tts_url got missed.
#
# `telemetry.otlp_endpoint` is here for the same reason as tts_url and needs
# no separate argument: OTel spans carry transcripts, prompts, and tool output,
# so a repo that names the collector reads the whole session.
#
# `grounding.require_provenance` is here for a fourth reason: it does not run,
# redirect, or write anything — it WEAKENS a safety control. While the default
# is off a repo could only tighten it, but the moment M13's measurement flips
# that default, an untrusted repo setting it back to false would silently
# disable provenance checking on its own code. Grounding is a hard requirement
# (CLAUDE.md), so who may relax it is exactly the kind of decision this list is
# for, and deciding it now costs nothing.
PRIVILEGED_PATHS: tuple[str, ...] = (
    "mcp_servers",
    "providers",
    "voice.tts_url",
    "grounding.require_provenance",
    "telemetry.otlp_endpoint",
)

# Safe unless they point at a provider the REPO defined — a repo may suggest
# `groq/llama-3.3`, but may not aim a slot at its own base_url. `vision` is
# here for the same reason as fast/deep and needs no separate argument: it is a
# model slot, and read_image posts an image from the repo to whatever it names.
CONDITIONAL_PATHS: tuple[str, ...] = ("fast", "deep", "vision", "fallbacks")

_EFFECTS = {
    "mcp_servers": "run a program",
    "providers": "send prompts and your API key to",
    "voice.tts_url": "send everything Pyrrhon says to",
    "telemetry.otlp_endpoint": "send traces — transcripts, prompts, tool output — to",
    "grounding.require_provenance": "relax or tighten citation checking",
    "fast": "choose the model for",
    "deep": "choose the model for",
    "vision": "choose the model that reads images for",
    "fallbacks": "choose the fallback models for",
}


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge; overlay wins on scalars, tables merge key-wise.

    The old `{**base, **overlay}` replaced whole tables, so a repo setting one
    key of [voice] silently deleted every global [voice] key beside it.
    Neither input is mutated: this runs on a security boundary and a caller
    should never find its own dict rewritten underneath it.
    """
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _describe(path: str, name: str, value: object) -> str:
    """The one line the user reads before approving. It must name the payload,
    not just the key — "run a program: indexer" tells them nothing."""
    verb = _EFFECTS.get(path, "change")
    target: object = value
    if isinstance(value, dict) and "provider" in value:
        # A model slot: "evil/anything" reads; the raw dict does not, and an
        # unreadable line is not informed consent.
        target = f"{value['provider']}/{value.get('model', '?')}"
    elif isinstance(value, dict):
        # base_url first: for a [providers.x] table it is the whole point,
        # and such a table has no `command`.
        target = value.get("base_url") or value.get("command") or value.get("url") or value
    elif isinstance(value, list):
        target = ", ".join(str(entry) for entry in value)
    return f"{verb}: {name} -> {target}"


def _providers_named(value: object) -> set[str]:
    """Provider names a slot or fallback list refers to."""
    if isinstance(value, dict) and "provider" in value:
        return {str(value["provider"])}
    if isinstance(value, dict):  # a {slot: [entries]} fallbacks table
        names: set[str] = set()
        for entries in value.values():
            if isinstance(entries, str):  # tolerate a scalar where a list belongs
                entries = [entries]
            for entry in entries or ():
                names.add(str(entry).partition("/")[0])
        return names
    return set()


def partition_repo_config(
    repo_data: dict, global_data: dict, granted: TrustFile
) -> tuple[dict, list[Grant]]:
    """Split a repo's .pyrrhon.toml into (applied now, pending consent).

    Everything not named in PRIVILEGED_PATHS/CONDITIONAL_PATHS merges normally:
    the default is trust, and the quarantine list is the reviewed exception.
    That ordering is deliberate — a new harmless key should not need a code
    change to work, but a new dangerous one is a decision someone has to make.
    """
    allowed = {k: v for k, v in repo_data.items() if k not in PRIVILEGED_PATHS}
    pending: list[Grant] = []

    for table in ("mcp_servers", "providers"):
        for name, value in (repo_data.get(table) or {}).items():
            grant = Grant(
                "config", f"{table}.{name}", digest_value(value),
                _describe(table, name, value),
            )
            if granted.has(grant):
                allowed.setdefault(table, {})[name] = value
            else:
                pending.append(grant)

    # Dotted leaves — voice.tts_url, grounding.require_provenance,
    # telemetry.otlp_endpoint. Each holds ONE key back and lets the rest of its
    # table through. Driven by PRIVILEGED_PATHS rather than hand-written once
    # per key: a privileged leaf someone forgets to add a block for is exactly
    # how tts_url was missed the first time, and the reasons differ per key
    # while the mechanism does not.
    for path in PRIVILEGED_PATHS:
        table_name, _, key = path.partition(".")
        if not key:
            continue  # whole-table paths are handled above
        table = dict(repo_data.get(table_name) or {})
        if key in table:
            value = table.pop(key)
            grant = Grant(
                "config", path, digest_value(value), _describe(path, key, value),
            )
            if granted.has(grant):
                table[key] = value
            else:
                pending.append(grant)
        if table_name in repo_data:
            allowed[table_name] = table

    # A slot may only name a provider that is builtin, global, or already
    # granted above — otherwise the repo controls where the key goes.
    safe_providers = (
        set(BUILTIN_PROVIDERS)
        | set(global_data.get("providers") or {})
        | set(allowed.get("providers") or {})
    )
    for path in CONDITIONAL_PATHS:
        value = repo_data.get(path)
        if value is None:
            continue
        if _providers_named(value) <= safe_providers:
            continue
        allowed.pop(path, None)
        pending.append(
            Grant("config", path, digest_value(value), _describe(path, path, value))
        )
    return allowed, pending


def load_settings(
    repo_root: Path, home: Path | None = None, granted: TrustFile | None = None
) -> Settings:
    """Global config, deep-merged with the repo's *granted* config.

    `granted` defaults to the repo's own .pyrrhon/trusted file, so every
    existing call site gets the safe behaviour without changing. Ungranted
    privileged keys land in `settings.pending_grants` and are never applied —
    consumers like MCPManager therefore need no knowledge of trust at all.
    """
    home = home or Path.home()
    global_data = _read_toml(home / ".pyrrhon" / "config.toml")
    repo_data = _read_toml(repo_root / ".pyrrhon.toml")
    trust = granted if granted is not None else read_trust_file(repo_root)
    allowed, pending = partition_repo_config(repo_data, global_data, trust)
    settings = Settings.model_validate(deep_merge(global_data, allowed))
    settings.pending_grants = pending
    return settings


def config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".pyrrhon" / "config.toml"


def patch_config(updates: dict[str, dict], home: Path | None = None) -> Path:
    """Deep-merge {section: {key: value}} into the global config.toml.

    Existing sections and keys survive (this is how /settings edits one knob
    without clobbering the rest); a value of None clears that key. Mirrors the
    wizard's writer — comments are not preserved, and keys never live here
    (they go to credentials.toml). Returns the path written.
    """
    path = config_path(home)
    existing = _read_toml(path)
    for section, kv in updates.items():
        merged = {**existing.get(section, {}), **kv}
        existing[section] = {k: v for k, v in merged.items() if v is not None}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(
            b"# Managed by Pyrrhon (`/settings` and `pyrrhon --setup`). Editing "
            b"here is fine; rerunning merges sections and drops comments.\n"
        )
        tomli_w.dump(existing, f)
    return path
