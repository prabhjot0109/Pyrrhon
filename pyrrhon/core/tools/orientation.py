"""The first thing worth knowing about a repo you have never opened.

Act 1's premise is a codebase you didn't write, and the session currently
starts with a blank prompt — the user has to know enough to ask a first
question, which is exactly what they don't have yet.

Emitted as a ScreenArtifact, deliberately: it is a dense census of languages,
files and counts, and VOICE_STYLE forbids reading tables and path lists aloud.
The voice channel says one sentence about it; the screen carries the detail.
This is the event type's first real use since M0 defined it.
"""

from __future__ import annotations

from pathlib import Path

from pyrrhon.core.events import ScreenArtifact
from pyrrhon.core.tools.ast_index import SymbolIndex
from pyrrhon.core.tools.git import GitLogTool

MAP_CHARS = 1500
RECENT_COMMITS = 5


async def build_orientation(repo_root: Path, index: SymbolIndex) -> ScreenArtifact:
    await index.ensure_fresh()
    census = await index.languages()
    if not census:
        return ScreenArtifact(
            kind="markdown",
            content=(
                f"## {repo_root.name}\n\n"
                "No indexed source found — no files in a language Pyrrhon "
                "indexes yet (python, typescript, javascript, go). Ask about "
                "any file directly and it will read it."
            ),
        )
    languages = ", ".join(f"{lang} ({count})" for lang, count in census.items())
    repo_map = await index.build_repo_map(max_chars=MAP_CHARS)
    sections = [
        f"## {repo_root.name}",
        "",
        f"**Languages:** {languages}",
        "",
        "**Most-referenced code**",
        "",
        f"```\n{repo_map}\n```",
    ]
    # Tools return "ERROR: ..." rather than raising, so an unchecked pass-through
    # would render a git failure as though it were history. A repo with no git
    # dir simply has no commits section — that is honest; a printed error is not.
    recent = await GitLogTool(repo_root).run(max_count=RECENT_COMMITS)
    if not recent.startswith("ERROR"):
        sections += ["", "**Recent commits**", "", f"```\n{recent}\n```"]
    return ScreenArtifact(kind="markdown", content="\n".join(sections))
