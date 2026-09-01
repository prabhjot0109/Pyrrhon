"""What the session knows about itself, rendered into the model's context.

M18. `bootstrap.orient_in_background` renders `build_orientation` as a
ScreenArtifact for the USER; the model was given one line, the repo root path.
So the user opened every session with a ranked map of the repo and the model
opened it blind, and spent its first round re-deriving what had already been
computed and shown on screen.

Two blocks, kept apart because they cost different things and arrive at
different times.

The environment is a handful of facts. Date and platform are free, so they are
rendered every turn; branch and dirtiness cost a subprocess each, so they
arrive with the background walk and are simply absent before it finishes.
Today's date is the load-bearing one: "what changed last week" is unanswerable
without it, and a model with no date in context answers it from its training
cutoff and sounds certain doing so.

The brief is a bounded slice of the repo map, which does not exist until the
symbol index is warm. It arrives on whatever turn follows the background walk
rather than blocking the first prompt on a cold index — the same trade
`orient_in_background` already made for the screen.

**Neither block is an admissible citation source, and that is enforced here
rather than asked for.** A map is not a tool result: it says where a thing
lives, not what any line of it says, and a line number carried in a system
prompt outlives the read that justified it in exactly the way M16e's
compaction summaries did. `_without_line_numbers` runs over everything before
it can reach history, so `tests/test_history_invariant.py`'s rule holds for
the brief the same way it holds for a summary.

The risk this milestone runs is the mirror of M16e's: a prompt carrying a repo
map can make a model stop looking and answer from the map. The brief says out
loud what it is and what it is for, and a rise in the gate's `stripped` arm is
the symptom to watch.
"""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from pyrrhon.core.grounding.citations import strip_line_numbers
from pyrrhon.core.tools.ast_index import SymbolIndex

# ~375 tokens, against MAX_SOUL_CHARS' 6000. Smaller than the soul budget on
# purpose: a soul file is something the user chose to say and the brief is
# something we decided to send, and both are paid on every round of every turn.
MAX_BRIEF_CHARS = 1500

# The map's own header and framing, subtracted from the budget so the cap is
# the size of the block rather than the size of one part of it.
_BRIEF_OVERHEAD = 320

# The repo map renders a symbol row as "  kind name:line (n refs)". The line is
# the part that goes stale, and it is the part the model has no business
# repeating: the row already names the file above it, which is all the brief is
# for. Anchored to the row shape rather than to digits anywhere, so a symbol
# that legitimately ends in a number keeps its name.
_SYMBOL_LINE_RE = re.compile(r"^(?P<row>\s+\S+ \S+):\d+", re.MULTILINE)

# Long enough that a hung git (a network filesystem, an index lock held by
# another process) does not delay the brief indefinitely; short enough that the
# wait is invisible next to the index walk it runs beside.
_GIT_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class SessionContext:
    """Everything the model is told about the session it is in.

    Frozen and replaced wholesale rather than mutated field by field: the
    background walk writes it from a worker thread while the turn loop reads
    it, and a whole-object swap cannot be read half-updated. `Agent` holds one
    of these and the turn loop renders it; nothing here reaches back.
    """

    repo_root: Path
    branch: str | None = None
    dirty: bool | None = None
    brief: str = ""


def _git(repo_root: Path, *args: str) -> str | None:
    """One git command, or None for every way it can fail to answer.

    None means "we do not know", which the renderer omits. It never means
    "clean" or "no branch": a guess about the tree state is worse than
    silence, because the model states what it is told.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def capture_git_state(repo_root: Path) -> tuple[str | None, bool | None]:
    """The branch name and whether tracked files are modified.

    Untracked files are deliberately excluded (`-uno`). Walking for them is the
    expensive half of `git status` on a large repo, and "you have a scratch
    file lying around" is not what the model needs to know. What it needs is
    whether what it is reading matches what is committed, and that is exactly
    the tracked-file question.
    """
    head = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = head.strip() if head else None
    if branch == "HEAD":
        branch = None  # detached: a name would be a lie, and the SHA is noise
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=no")
    dirty = bool(status.strip()) if status is not None else None
    return branch, dirty


def _without_line_numbers(text: str) -> str:
    """Strip every coordinate the brief could carry, symbol rows first.

    Two passes because they catch different shapes. `strip_line_numbers` knows
    `path:line` and is what M16e already uses on compaction summaries; the
    symbol rows are `name:line`, which carries no extension and so is invisible
    to the citation regex — and therefore invisible to the gate as well, which
    is what makes it worth removing rather than trusting.
    """
    return strip_line_numbers(_SYMBOL_LINE_RE.sub(r"\g<row>", text))


async def build_repo_brief(
    index: SymbolIndex, max_chars: int = MAX_BRIEF_CHARS
) -> str:
    """The map the user already sees, bounded and stripped, for the model.

    Returns "" for a repo with nothing indexed, which is a normal case rather
    than a failure: the renderer then omits the section entirely instead of
    telling the model that the repo is empty when it merely holds languages we
    do not parse.
    """
    await index.ensure_fresh()
    census = await index.languages()
    if not census:
        return ""
    languages = ", ".join(f"{lang} ({count})" for lang, count in census.items())
    body = await index.build_repo_map(max_chars=max(0, max_chars - _BRIEF_OVERHEAD))
    return f"Languages: {languages}\n\n{_without_line_numbers(body)}"


async def capture_session_context(
    context: SessionContext, index: SymbolIndex
) -> SessionContext:
    """Fill in everything that costs a walk or a subprocess.

    Called from the background orientation task, so a cold index and a slow
    git never sit in front of the first prompt. A failure of either half
    leaves that half unknown and the other half intact, because a brief with
    no branch on it is still worth most of what the brief is worth.
    """
    branch, dirty = capture_git_state(context.repo_root)
    return replace(
        context, branch=branch, dirty=dirty, brief=await build_repo_brief(index)
    )


def render_session_context(
    context: SessionContext,
    *,
    mode: str,
    voice_active: bool,
    today: date | None = None,
) -> str:
    """The block appended to the system message, or "" when there is nothing.

    Appended AFTER the delivery style rather than into `system_prompt`, which
    is deliberate: `system_prompt` is the stable prefix a provider caches, and
    the style block already varies with `/voice`. Everything that changes
    within a session therefore lives on one side of the cache boundary
    instead of splitting the prefix into a family per session state.
    """
    facts = [
        f"Today is {(today or date.today()).isoformat()}. "
        f"Pyrrhon is running on {platform.system() or 'an unknown platform'}."
    ]
    where = f"The repo under discussion is at {context.repo_root}"
    if context.branch:
        where += f", on branch {context.branch}"
    if context.dirty is not None:
        where += (
            ", with uncommitted changes to tracked files"
            if context.dirty
            else ", with a clean working tree"
        )
    facts.append(where + ".")
    facts.append(
        f"You are in {mode} mode, and you are being "
        + ("spoken aloud." if voice_active else "read on screen.")
    )
    lines = ["# This session", "", *(f"- {fact}" for fact in facts)]
    if context.brief:
        lines += [
            "",
            "## What is in this repo",
            "",
            "A map, not a tool result. It tells you where to look FIRST; it is "
            "not evidence and carries no line numbers, so nothing in it is "
            "citable. Open the file before you claim anything about what is in "
            "it.",
            "",
            context.brief,
        ]
    return "\n".join(lines)
