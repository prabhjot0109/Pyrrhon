"""The provider table: shape, uniqueness, and the invariants the factory relies on."""

import ast
import importlib.util
import pathlib
import tomllib

from pyrrhon.voice.registry import (
    PIPER_HTTP,
    VOICE_PROVIDERS,
    find,
    stt_providers,
    tts_providers,
)


def test_ids_are_unique_within_each_kind():
    for kind in ("stt", "tts"):
        ids = [p.id for p in VOICE_PROVIDERS if p.kind == kind]
        assert len(ids) == len(set(ids)), f"duplicate {kind} id"


def test_keyless_providers_declare_no_key_env():
    for provider in VOICE_PROVIDERS:
        if provider.id in ("whisper-local", "moonshine", "piper", "kokoro"):
            assert provider.key_env is None, f"{provider.id} should be keyless"


def test_every_provider_names_a_pipecat_or_pyrrhon_module():
    for provider in VOICE_PROVIDERS:
        assert provider.module.startswith(("pipecat.", "pyrrhon.")), provider.id


def test_find_returns_none_for_unknown():
    assert find("tts", "definitely-not-a-provider") is None
    assert find("tts", "piper") is not None


def test_split_helpers_agree_with_the_table():
    assert set(stt_providers()) | set(tts_providers()) == set(VOICE_PROVIDERS)


# -- Tier 1 (existence) and Tier 2 (reachability) ----------------------------
#
# The gate that makes "advertised but uninstallable" a test failure rather
# than something a user discovers at a terminal.

def _class_exists(module: str, cls: str) -> tuple[bool, str]:
    """Confirm module.cls exists WITHOUT importing it.

    Importing would require the provider's extra to be installed, and the
    providers whose extras are absent are exactly the ones that used to ship
    broken. find_spec + an AST scan checks the source on disk instead.
    """
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError:
        return False, f"module {module} not found"
    if spec is None or not spec.origin:
        return False, f"module {module} has no source"
    source = pathlib.Path(spec.origin).read_text(encoding="utf-8")
    names = {n.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef)}
    if cls not in names:
        return False, f"class {cls} not in {module}"
    return True, ""


def test_tier1_every_provider_class_exists():
    """A pipecat bump that renames or drops a class fails HERE, not in a terminal."""
    failures = []
    for provider in (*VOICE_PROVIDERS, PIPER_HTTP):
        ok, why = _class_exists(provider.module, provider.cls)
        if not ok:
            failures.append(f"{provider.kind}/{provider.id}: {why}")
    assert not failures, "providers whose class is missing:\n" + "\n".join(failures)


def _declared_extras() -> set[str]:
    root = pathlib.Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = list(data["project"]["dependencies"])
    for extra in data["project"].get("optional-dependencies", {}).values():
        deps.extend(extra)
    extras: set[str] = set()
    for dep in deps:
        if dep.startswith("pipecat-ai[") and "]" in dep:
            extras.update(dep[len("pipecat-ai[") : dep.index("]")].split(","))
    return {e.strip() for e in extras}


def test_tier2_bundled_providers_have_their_extra_declared():
    """Providers we ship ON by default must be installable by `uv sync --extra voice`."""
    bundled = {("tts", "piper"), ("stt", "groq"), ("stt", "openai"), ("tts", "openai")}
    declared = _declared_extras()
    for kind, pid in bundled:
        provider = find(kind, pid)
        assert provider is not None, f"{kind}/{pid} missing from the table"
        if provider.extra:
            assert provider.extra in declared, (
                f"{kind}/{pid} ships on by default but its extra "
                f"'{provider.extra}' is not in pyproject.toml"
            )


def test_tier2_optional_providers_name_an_extra_users_can_install():
    """Anything not bundled must at least tell the user what to run."""
    for provider in VOICE_PROVIDERS:
        if provider.module.startswith("pyrrhon."):
            continue  # in-repo shims need no pipecat extra
        assert provider.extra, (
            f"{provider.kind}/{provider.id} names no extra, so the catalog "
            "cannot tell the user how to install it"
        )
