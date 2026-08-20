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

from pyrrhon.config.catalog import (
    LLM_CHOICES,
    ProviderChoice,
    stt_choices,
    tts_choices,
)
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
        stt = _choose(console, ask, "Speech-to-text (your voice -> text):",
                      stt_choices(), stored, allow_back=True)
        state["stt"] = stt
        _collect_key(console, secret, stt, state["keys"], stored)
        tts = _choose(console, ask, "Text-to-speech (Pyrrhon's voice):",
                      tts_choices(), stored, allow_back=True)
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
