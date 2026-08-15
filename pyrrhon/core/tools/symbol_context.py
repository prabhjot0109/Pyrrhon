"""symbol_context: everything about one symbol, in a single tool round.

"How does X work?" used to cost three model round trips — find_symbol, then
find_references, then read_file — and at voice latency a round trip is a whole
model turnaround, not a function call. M10 measured this as the largest
remaining structural cost in the loop.

The output deliberately keeps the `path:line` shape every other tool uses: the
model cites from it, the grounding gate parses it, and the M13 evidence ledger
harvests it. Changing the shape here would silently break all three.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pyrrhon.core.tools.ast_index import SymbolIndex
from pyrrhon.core.tools.base import Tool

MAX_REFERENCES = 20
DEFAULT_CONTEXT_LINES = 20


class SymbolContextTool(Tool):
    name = "symbol_context"
    description = (
        "Everything about one symbol in a single call: where it is defined, the "
        "source around the definition, what calls it, and the file's import "
        "edges. Prefer this over separate definition/reference/dependency "
        "lookups — it is one round trip instead of three."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact symbol name, e.g. 'run_turn'"},
            "context_lines": {
                "type": "integer",
                "description": (
                    f"Source lines to show around the definition "
                    f"(default {DEFAULT_CONTEXT_LINES})"
                ),
            },
        },
        "required": ["name"],
    }

    def __init__(self, index: SymbolIndex, root: Path):
        self.index = index
        self.root = root

    async def run(self, name: str, context_lines: int = DEFAULT_CONTEXT_LINES) -> str:
        await self.index.ensure_fresh()
        definitions = await self.index.find_symbol(name)
        if not definitions:
            return f"No definition found for '{name}'."
        context_lines = max(0, min(int(context_lines), 100))

        file, line, kind = definitions[0]
        sections = [f"{file}:{line}: {kind} {name}"]
        if len(definitions) > 1:
            sections.append("also defined at:")
            sections += [f"  {f}:{n}: {k} {name}" for f, n, k in definitions[1:]]

        source = await asyncio.to_thread(self._window, file, line, context_lines)
        sections += ["", "source:", source]

        references = await self.index.find_references(name)
        sections += ["", f"called from ({len(references)} site(s)):"]
        sections += [f"  {f}:{n}" for f, n in references[:MAX_REFERENCES]] or ["  (none)"]
        if len(references) > MAX_REFERENCES:
            sections.append(f"  …and {len(references) - MAX_REFERENCES} more")

        imports = await self.index.list_imports(file)
        importers = await self.index.find_importers(file)
        sections += ["", f"{file} imports: " + (", ".join(imports) or "(none)")]
        sections.append(f"{file} imported by: " + (", ".join(importers) or "(none)"))
        return "\n".join(sections)

    def _window(self, rel: str, line: int, context_lines: int) -> str:
        """Numbered source around the definition — same gutter format as
        read_file, which is what the evidence ledger parses."""
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            return "(source unavailable)"
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "(source unavailable)"
        first = max(1, line - 2)
        last = min(len(lines), line + context_lines)
        return "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1))
