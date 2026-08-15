"""Which languages the symbol index understands, and how.

M4 shipped Python-only with one module-level grammar and three module-level
queries, and `_iter_files_with_mtime` hardcoded `.py`. That shape makes a new
grammar a rewrite rather than a table entry — and worse, a grammar added
without touching the walk silently indexes nothing, because no file with that
extension is ever yielded. M10's postscript flagged exactly this.

Grammars compile lazily and are cached: a Python-only repo must not pay to
load the Go grammar, and `get_language` plus three `Query` compilations is real
startup cost per language.

Import semantics differ per language and are not expressible as a tree-sitter
query, so each spec carries a small text parser for the import statements its
query captured. A false import edge is harmless; a missing one is not.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from tree_sitter import Language, Query
from tree_sitter_language_pack import get_language


@dataclass(frozen=True)
class LanguageSpec:
    name: str                      # tree-sitter-language-pack grammar name
    extensions: tuple[str, ...]
    def_query: str                 # captures named @def.<kind>
    ref_query: str                 # captures named @ref
    import_query: str              # captures whole import statements as @import
    parse_imports: Callable[[str, str], list[str]]  # (statement_text, package) -> modules


@dataclass(frozen=True)
class CompiledLanguage:
    language: Language
    defs: Query
    refs: Query
    imports: Query


@lru_cache(maxsize=None)
def compiled(spec: LanguageSpec) -> CompiledLanguage:
    """Grammar + queries for one spec, compiled once per process."""
    language = get_language(spec.name)  # type: ignore[arg-type]
    return CompiledLanguage(
        language=language,
        defs=Query(language, spec.def_query),
        refs=Query(language, spec.ref_query),
        imports=Query(language, spec.import_query),
    )


# -- python (moved verbatim from ast_index.py) -----------------------------

_PY_DEFS = """
(function_definition name: (identifier) @def.function)
(class_definition name: (identifier) @def.class)
"""

# "References" = call sites: plain calls and method calls.
_PY_REFS = """
(call function: (identifier) @ref)
(call function: (attribute attribute: (identifier) @ref))
"""

# Whole import statements — their text is parsed in Python, which is robust
# across grammar details.
_PY_IMPORTS = """
(import_statement) @import
(import_from_statement) @import
"""


def _parse_python_imports(stmt_text: str, package: str) -> list[str]:
    """Module names referenced by one import statement.

    from-imports also record `module.name` for each imported name: the name
    may be a submodule (`from pkg import api`) or an attribute — a false
    attribute edge is harmless, a missed submodule edge is not.
    """
    text = " ".join(stmt_text.split())
    if text.startswith("from "):
        module_part, _, names_part = text[len("from "):].partition(" import ")
        module = _resolve_relative(module_part.strip(), package)
        if not module:
            return []
        if names_part.strip() == "*":
            return [module]
        modules = [module]
        for name in names_part.replace("(", "").replace(")", "").split(","):
            name = name.strip().split(" as ")[0].strip()
            if name:
                modules.append(f"{module}.{name}")
        return modules
    modules = []
    for part in text[len("import "):].split(","):
        module = part.strip().split(" as ")[0].strip()
        if module:
            modules.append(module)
    return modules


def _resolve_relative(module: str, package: str) -> str:
    if not module.startswith("."):
        return module
    dots = len(module) - len(module.lstrip("."))
    remainder = module.lstrip(".")
    parts = package.split(".") if package else []
    if dots - 1:
        parts = parts[: -(dots - 1)] if len(parts) >= dots - 1 else []
    base = ".".join(parts)
    if remainder and base:
        return f"{base}.{remainder}"
    return remainder or base


# -- typescript / javascript ------------------------------------------------

# TS and JS diverge on one node only: a class name is a `type_identifier` in
# TypeScript and a plain `identifier` in JavaScript. Everything else is shared.
_TS_DEFS = """
(function_declaration name: (identifier) @def.function)
(class_declaration name: (type_identifier) @def.class)
(method_definition name: (property_identifier) @def.method)
"""
_JS_DEFS = """
(function_declaration name: (identifier) @def.function)
(class_declaration name: (identifier) @def.class)
(method_definition name: (property_identifier) @def.method)
"""
_JSTS_REFS = """
(call_expression function: (identifier) @ref)
(call_expression function: (member_expression property: (property_identifier) @ref))
"""
# The require() arm captures the WHOLE call_expression, not the `require`
# identifier: the module specifier lives in the arguments, and the text parser
# below reads it out of the statement text. Capturing the identifier alone
# would match, parse to nothing, and leave a silently dead import edge.
_JSTS_IMPORTS = """
(import_statement) @import
((call_expression function: (identifier) @_fn) @import
 (#eq? @_fn "require"))
"""

_QUOTED = re.compile(r"""["']([^"']+)["']""")


def _parse_js_imports(text: str, _package: str) -> list[str]:
    """Module specifiers from an ES import or a require() call."""
    return _QUOTED.findall(text)


# -- go ---------------------------------------------------------------------

_GO_DEFS = """
(function_declaration name: (identifier) @def.function)
(method_declaration name: (field_identifier) @def.method)
(type_declaration (type_spec name: (type_identifier) @def.type))
"""
_GO_REFS = """
(call_expression function: (identifier) @ref)
(call_expression function: (selector_expression field: (field_identifier) @ref))
"""
_GO_IMPORTS = "(import_declaration) @import"

_GO_QUOTED = re.compile(r'"([^"]+)"')


def _parse_go_imports(text: str, _package: str) -> list[str]:
    """Every quoted path in an import declaration, single or block form."""
    return _GO_QUOTED.findall(text)


LANGUAGES: tuple[LanguageSpec, ...] = (
    LanguageSpec("python", (".py",), _PY_DEFS, _PY_REFS, _PY_IMPORTS, _parse_python_imports),
    LanguageSpec("typescript", (".ts",), _TS_DEFS, _JSTS_REFS, _JSTS_IMPORTS, _parse_js_imports),
    LanguageSpec("tsx", (".tsx",), _TS_DEFS, _JSTS_REFS, _JSTS_IMPORTS, _parse_js_imports),
    LanguageSpec(
        "javascript",
        (".js", ".mjs", ".cjs", ".jsx"),
        _JS_DEFS,
        _JSTS_REFS,
        _JSTS_IMPORTS,
        _parse_js_imports,
    ),
    LanguageSpec("go", (".go",), _GO_DEFS, _GO_REFS, _GO_IMPORTS, _parse_go_imports),
)

_BY_EXTENSION = {ext: spec for spec in LANGUAGES for ext in spec.extensions}
INDEXABLE_EXTENSIONS = frozenset(_BY_EXTENSION)


def spec_for_extension(ext: str) -> LanguageSpec | None:
    return _BY_EXTENSION.get(ext.lower())
