"""Soul files: user-authored markdown loaded into the system prompt each session.

Users create them with /init (or by hand) in ~/.pyrrhon/ and <repo>/.pyrrhon/.
Global loads first, repo last — so repo-level context wins.
"""

from __future__ import annotations

from pathlib import Path

from pyrrhon.core.agent.prompts import SYSTEM_PROMPT


def load_soul(repo_root: Path, home: Path | None = None) -> str:
    home = home or Path.home()
    sections: list[str] = []
    for directory in (home / ".pyrrhon", repo_root / ".pyrrhon"):
        if not directory.is_dir():
            continue
        for md in sorted(directory.glob("*.md")):
            content = md.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"## From {md.name}\n\n{content}")
    return "\n\n".join(sections)


def build_system_prompt(repo_root: Path, home: Path | None = None) -> str:
    prompt = SYSTEM_PROMPT + f"\nThe repo under discussion is rooted at: {repo_root}\n"
    soul = load_soul(repo_root, home)
    if soul:
        prompt += f"\n# User context (soul files)\n\n{soul}\n"
    return prompt
