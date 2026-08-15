"""Module-level import graph in the SymbolIndex."""

from pyrrhon.core.tools.ast_index import (
    DependenciesTool,
    SymbolIndex,
    _module_name,
    _package_of,
)
from pyrrhon.core.tools.languages import _parse_python_imports as _modules_from_import


def test_module_and_package_names():
    assert _module_name("pkg/sub/mod.py") == "pkg.sub.mod"
    assert _module_name("pkg/sub/__init__.py") == "pkg.sub"
    assert _package_of("pkg/sub/mod.py") == "pkg.sub"
    assert _package_of("pkg/sub/__init__.py") == "pkg.sub"
    assert _package_of("app.py") == ""


def test_modules_from_import_statements():
    assert _modules_from_import("import os", "") == ["os"]
    assert _modules_from_import("import a.b, c as d", "") == ["a.b", "c"]
    # from-imports record the module AND each name as a candidate submodule,
    # so `from pkg import mod` still creates an edge to pkg.mod.
    assert _modules_from_import("from pkg.core import session, events", "") == [
        "pkg.core", "pkg.core.session", "pkg.core.events",
    ]
    assert _modules_from_import("from .utils import helper", "pkg.sub") == [
        "pkg.sub.utils", "pkg.sub.utils.helper",
    ]
    assert _modules_from_import("from ..core import gate", "pkg.sub") == [
        "pkg.core", "pkg.core.gate",
    ]
    assert _modules_from_import("from x import *", "") == ["x"]


def _make_repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "service.py").write_text(
        "def handle():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "api.py").write_text(
        "from pkg.service import handle\n\ndef route():\n    return handle()\n",
        encoding="utf-8",
    )
    (tmp_path / "cli.py").write_text(
        "from pkg import api\n\ndef main():\n    return api.route()\n",
        encoding="utf-8",
    )
    return tmp_path


async def test_list_imports_and_find_importers(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    await index.ensure_fresh()
    assert "pkg.service" in await index.list_imports("pkg/api.py")
    importers = await index.find_importers("pkg/service.py")
    assert importers == ["pkg/api.py"]
    # `from pkg import api` in cli.py creates the candidate edge pkg.api:
    assert await index.find_importers("pkg/api.py") == ["cli.py"]


async def test_dependencies_tool_formats_both_directions(tmp_path):
    index = SymbolIndex(_make_repo(tmp_path))
    out = await DependenciesTool(index).run(path="pkg/api.py")
    assert "imports:" in out
    assert "pkg.service" in out
    assert "imported by:" in out
    assert "cli.py" in out


async def test_cyclic_imports_do_not_break_queries(tmp_path):
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    assert await index.find_importers("a.py") == ["b.py"]
    assert await index.find_importers("b.py") == ["a.py"]
