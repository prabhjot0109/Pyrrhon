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


def test_tier1_every_provider_declares_a_settings_class():
    """The factory sends model and voice as `settings=Cls.Settings(...)`.

    That is only safe because pipecat declares `Settings` on every service and
    canonicalized the field names — which is what let the table drop its
    `model_kwarg`/`voice_kwarg` columns. AST again, not import: the rows whose
    extra is absent are exactly the ones that used to ship broken.
    """
    failures = []
    for provider in (*VOICE_PROVIDERS, PIPER_HTTP):
        if provider.module.startswith("pyrrhon."):
            continue  # in-repo shims are checked by their own unit tests
        ok, why = _class_declares_settings(provider.module, provider.cls)
        if not ok:
            failures.append(f"{provider.kind}/{provider.id}: {why}")
    assert not failures, "rows whose class declares no Settings:\n" + "\n".join(
        failures
    )


def _class_declares_settings(module: str, cls: str) -> tuple[bool, str]:
    """True if module.cls assigns `Settings` in its own body.

    Deliberately does NOT follow base classes. Every concrete service in the
    table declares its own `Settings` today, so requiring it here is accurate
    rather than lenient — and if a future pipecat moves the attribute up to a
    shared base, this test fails and a human decides, which is the drift signal
    the tier exists for. A helper that walked bases would have to give up at
    the first cross-module one and pass vacuously, which is precisely the
    shape of test Phase 3 replaced.
    """
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.origin:
        return False, f"module {module} has no source"
    tree = ast.parse(pathlib.Path(spec.origin).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != cls:
            continue
        for stmt in node.body:
            targets = getattr(stmt, "targets", None) or [getattr(stmt, "target", None)]
            if any(isinstance(t, ast.Name) and t.id == "Settings" for t in targets):
                return True, ""
        return False, f"{cls} declares no Settings attribute"
    return False, f"class {cls} not in {module}"


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


def test_tier2_every_table_row_ships_installed():
    """A row in the menu must be runnable after one `uv sync`, with no extra.

    This used to check a hand-listed set of four "bundled" providers, which
    was the honest test while the rest lived behind `--extra voice`. There is
    no such distinction now: every extra the table names is in the base
    dependency line, so the check is over the whole table. Adding a row with
    a new extra fails here until pyproject.toml catches up, which is the
    point — a menu entry Pyrrhon cannot start is the failure mode.
    """
    declared = _declared_extras()
    for provider in (*VOICE_PROVIDERS, PIPER_HTTP):
        if not provider.extra:
            continue
        assert provider.extra in declared, (
            f"{provider.kind}/{provider.id} is offered in the menu but its "
            f"extra '{provider.extra}' is not in pyproject.toml's "
            "pipecat-ai[...] line"
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


def test_tier2_every_named_extra_is_one_pipecat_actually_declares():
    """A typo'd extra would render an install command that cannot work.

    availability() no longer reads pipecat's extra metadata — it asks each
    module what it imports, which is finer-grained. The extra name survives as
    the thing the install command tells the user to run, and this is the only
    check left on it.
    """
    from importlib import metadata

    declared = set(metadata.metadata("pipecat-ai").get_all("Provides-Extra") or [])
    for provider in (*VOICE_PROVIDERS, PIPER_HTTP):
        if provider.extra:
            assert provider.extra in declared, (
                f"{provider.kind}/{provider.id} names extra '{provider.extra}', "
                "which pipecat-ai does not declare"
            )
