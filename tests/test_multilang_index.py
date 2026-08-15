"""Indexing TS/JS/Go end to end.

The `polyglot_repo` fixture is a disposable COPY (tests/conftest.py): indexing
writes .pyrrhon/cache.db, and the fence there fails any test that does it to
the checked-in fixture tree.
"""

from pyrrhon.core.tools.ast_index import SymbolIndex


async def test_typescript_definitions_are_indexed(polyglot_repo):
    index = SymbolIndex(polyglot_repo)
    await index.ensure_fresh()
    assert await index.find_symbol("Greeter") == [("app.ts", 3, "class")]


async def test_javascript_definitions_are_indexed(polyglot_repo):
    index = SymbolIndex(polyglot_repo)
    await index.ensure_fresh()
    rows = await index.find_symbol("formatName")
    assert ("helpers.js", 1, "function") in rows


async def test_go_definitions_are_indexed(polyglot_repo):
    index = SymbolIndex(polyglot_repo)
    await index.ensure_fresh()
    assert await index.find_symbol("Server") == [("server.go", 5, "type")]


async def test_cross_file_references_are_found(polyglot_repo):
    index = SymbolIndex(polyglot_repo)
    await index.ensure_fresh()
    refs = await index.find_references("formatName")
    assert any(file == "app.ts" for file, _line in refs)


async def test_import_edges_cross_languages_without_resolving_across_them(polyglot_repo):
    """app.ts imports ./helpers.js; server.go imports fmt. Both are recorded as
    raw specifiers — resolving a TS specifier to a repo file is parked (M14
    Global Constraints), so this asserts the edge exists, not that it resolves.
    """
    index = SymbolIndex(polyglot_repo)
    await index.ensure_fresh()
    assert await index.list_imports("app.ts") == ["./helpers.js"]
    assert await index.list_imports("server.go") == ["fmt"]


async def test_a_language_census_is_available(polyglot_repo):
    index = SymbolIndex(polyglot_repo)
    await index.ensure_fresh()
    census = await index.languages()
    assert census == {"typescript": 1, "javascript": 1, "go": 1}


async def test_python_indexing_is_unchanged(tmp_path):
    (tmp_path / "m.py").write_text("def greet():\n    pass\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    assert await index.find_symbol("greet") == [("m.py", 1, "function")]


async def test_a_stale_cache_from_the_previous_schema_is_rebuilt(tmp_path):
    """CREATE TABLE IF NOT EXISTS cannot add the `lang` column, so an existing
    cache.db from M13 would keep the old shape and every query naming `lang`
    would fail. The version bump has to drop and rebuild."""
    import sqlite3

    (tmp_path / "m.py").write_text("def greet():\n    pass\n", encoding="utf-8")
    (tmp_path / ".pyrrhon").mkdir()
    old = sqlite3.connect(tmp_path / ".pyrrhon" / "cache.db")
    old.executescript(
        "CREATE TABLE files (path TEXT PRIMARY KEY, mtime REAL);"
        "CREATE TABLE symbols (name TEXT, kind TEXT, file TEXT, line INTEGER);"
        "CREATE TABLE refs (name TEXT, file TEXT, line INTEGER);"
        "CREATE TABLE imports (file TEXT, module TEXT);"
    )
    old.execute("INSERT INTO files (path, mtime) VALUES ('m.py', 1.0)")
    old.commit()
    old.close()

    index = SymbolIndex(tmp_path)
    await index.ensure_fresh()
    assert await index.find_symbol("greet") == [("m.py", 1, "function")]
    assert await index.languages() == {"python": 1}
