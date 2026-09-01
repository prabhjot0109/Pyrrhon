"""What a session leaves behind, so tomorrow can pick it up.

M19. Pyrrhon's stated use case is onboarding to a large codebase over a week,
and until now `history` died with the process: every morning started cold, at
the blank prompt, on a repo the user had already spent hours in. That is a
product hole rather than a convenience gap.

**Only the prose is persisted, and that is the design rather than a shortcut.**
A turn is recorded as the question the user asked and the answer Pyrrhon gave.
No tool results, no tool-call messages, no coordinates. Three things follow
from it, and the first is the one that made this safe to build now and not
before M16e.

Restored history is not admissible evidence. A resumed session's assistant
messages are gated prose, which M16e already strips of coordinates on the way
into history, and `_prose` strips them again here so it is an invariant of the
file rather than a dependency on another module's behaviour. So a resumed
session physically cannot cite from what it remembers — it has to reopen the
file — which is exactly what the admissibility rule asks for, enforced by the
shape of the data instead of by an instruction the model may ignore.

It also makes the file worth reading. A transcript full of tool JSON is a log;
a transcript of questions and answers is the artifact a user actually wants
after a two-hour walkthrough, which is what `/export` hands them.

And it is small. A week of sessions is kilobytes, so nothing here needs a
retention policy, a database, or a decision about when to throw work away.

**The log is not a projection of history, and the divergence is deliberate.**
Compaction rewrites `history` and summarizes early turns away; the log keeps
them. "What was said" and "what the model currently holds" are genuinely
different things, and the delivery contract has drawn that line since M15a.

**A turn is written at the START of the next one.** Barge-in truncation
arrives from the voice bridge after the turn's generator has finished, so a
record written at the end of a turn would preserve words the user cut off. One
turn of lag costs nothing and means the log always says what was heard, which
is the invariant `Session.truncate_last_assistant` exists to protect.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pyrrhon.core.events import Citation
from pyrrhon.core.grounding.citations import strip_line_numbers

log = logging.getLogger("pyrrhon.transcript")

# The two roles a persisted turn has. Deliberately not "tool": a tool message
# IS the admissible source, and persisting one would hand a resumed session
# evidence it did not gather. See the module docstring.
_ROLES = ("user", "assistant")


def _prose(text: str) -> str:
    """Text as history is allowed to hold it: no coordinates.

    Belt and braces over M16e, which already strips a gated answer on the way
    into history. Doing it again on the way out of the file makes it an
    invariant of the transcript rather than a property inherited from
    somewhere else — and a transcript outlives the reads that justified a line
    number by days rather than by turns, so it is the case that matters most.
    """
    return strip_line_numbers(text)


def repo_slug(repo_root: Path) -> str:
    """A per-repo directory name that is readable AND unique.

    The name alone collides — everybody has more than one `api` checkout — and
    a bare hash makes `ls ~/.pyrrhon/sessions` useless. Both, so the directory
    is browsable and two clones of the same project never share a history.
    """
    resolved = str(repo_root.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
    return f"{repo_root.name or 'repo'}-{digest}"


def sessions_dir(repo_root: Path, home: Path | None = None) -> Path:
    return (home or Path.home()) / ".pyrrhon" / "sessions" / repo_slug(repo_root)


@dataclass(frozen=True)
class TurnEntry:
    """One exchange, as it was said."""

    question: str
    answer: str
    citations: tuple[Citation, ...] = ()
    at: str = ""


@dataclass(frozen=True)
class SessionInfo:
    """A saved session, enough of it to choose between two in a list."""

    session_id: str
    path: Path
    started: str
    turns: int
    preview: str


class Transcript:
    """One session's file. Append-only; opened for a new session or a resumed one."""

    def __init__(self, path: Path):
        self.path = path

    @property
    def session_id(self) -> str:
        return self.path.stem

    @classmethod
    def start(cls, repo_root: Path, home: Path | None = None) -> Transcript:
        """A new session file, named so `ls` sorts by age.

        The random suffix is not paranoia: two channels opened in the same
        second is an ordinary thing to do, and the second one silently
        appending to the first one's file would interleave two conversations
        into one resume.
        """
        directory = sessions_dir(repo_root, home)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return cls(directory / f"{stamp}-{secrets.token_hex(2)}.jsonl")

    def record(
        self, question: str, answer: str, citations: tuple[Citation, ...] = ()
    ) -> None:
        """Append one exchange. Never raises: a session must survive a full disk.

        A failure to persist is logged and dropped rather than surfaced,
        because the alternative is killing a live conversation over the
        inability to write a convenience file.
        """
        entry = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "question": _prose(question),
            "answer": _prose(answer),
            "citations": [{"file": c.file, "line": c.line} for c in citations],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            log.debug("could not append to transcript %s", self.path, exc_info=True)

    def entries(self) -> list[TurnEntry]:
        """Everything in the file, skipping any line that does not parse.

        Skipping rather than failing, because the one time a line is truncated
        is a crash mid-write, and that is exactly the session a user is trying
        to recover.
        """
        found: list[TurnEntry] = []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return found
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log.debug("skipping unparseable transcript line in %s", self.path)
                continue
            found.append(
                TurnEntry(
                    question=str(record.get("question", "")),
                    answer=str(record.get("answer", "")),
                    citations=tuple(
                        Citation(file=c.get("file", ""), line=c.get("line"))
                        for c in record.get("citations") or ()
                        if isinstance(c, dict)
                    ),
                    at=str(record.get("at", "")),
                )
            )
        return found

    def messages(self) -> list[dict]:
        """The file as a chat history a resumed session can continue from.

        No system message: `Agent._run_turn` writes history[0] itself on every
        turn, so one here would be overwritten on the next turn and shadowed
        until then.
        """
        history: list[dict] = []
        for entry in self.entries():
            if entry.question:
                history.append({"role": "user", "content": entry.question})
            if entry.answer:
                history.append({"role": "assistant", "content": entry.answer})
        return history

    def covered_ground(self, limit: int = 12) -> str:
        """What this session has established, as a short list. "" when empty.

        The teaching product's version of a todo list. The voice style tells
        the model to offer the next hop "like a podcast" and nothing anywhere
        recorded what had already been covered, so a two-hour session had no
        visible spine — the thread existed only in the user's memory of it.

        The QUESTIONS rather than the answers, and that is the whole reason
        this is cheap enough to exist. A summary of what was concluded needs
        an LLM call and would go stale against a repo that moved; the
        questions are what the user asked, they are true forever, and they are
        the anchor someone actually scans for "where was I".

        Newest last, so it reads in the order it happened, and capped: a list
        long enough to scroll is one nobody reads.
        """
        entries = [entry for entry in self.entries() if entry.question]
        if not entries:
            return ""
        shown = entries[-limit:]
        lines = [f"**Covered so far** ({len(entries)} turn(s))", ""]
        if len(entries) > len(shown):
            lines.append(f"…{len(entries) - len(shown)} earlier turn(s)")
        lines += [f"- {entry.question}" for entry in shown]
        return "\n".join(lines)

    def to_markdown(self, repo_root: Path | None = None) -> str:
        """The artifact a user wants after a two-hour walkthrough.

        Citations are listed under the answer that earned them rather than
        inline, because the answer prose has had its coordinates stripped and
        re-inserting them would be guessing which sentence each belonged to.
        """
        entries = self.entries()
        title = f"# {repo_root.name}" if repo_root else "# Session"
        lines = [
            f"{title} — {self.session_id}",
            "",
            f"{len(entries)} turn(s), Pyrrhon.",
            "",
        ]
        for entry in entries:
            lines += [f"## {entry.question}", ""]
            if entry.at:
                lines += [f"*{entry.at}*", ""]
            lines += [entry.answer, ""]
            if entry.citations:
                lines.append("Sources:")
                lines += [
                    f"- `{c.file}:{c.line}`" if c.line else f"- `{c.file}`"
                    for c in entry.citations
                ]
                lines.append("")
        return "\n".join(lines)


def list_sessions(repo_root: Path, home: Path | None = None) -> list[SessionInfo]:
    """Saved sessions for this repo, newest first.

    Ordered by the name rather than by mtime: the name carries the START time,
    and a session resumed yesterday would otherwise sort above the one begun
    this morning, which is not what "newest" means to someone choosing.
    """
    directory = sessions_dir(repo_root, home)
    if not directory.is_dir():
        return []
    found: list[SessionInfo] = []
    for path in sorted(directory.glob("*.jsonl"), reverse=True):
        entries = Transcript(path).entries()
        if not entries:
            continue  # an empty session is noise in a list you choose from
        found.append(
            SessionInfo(
                session_id=path.stem,
                path=path,
                started=entries[0].at,
                turns=len(entries),
                preview=entries[0].question[:70],
            )
        )
    return found


def resolve_session(
    repo_root: Path, session_id: str | None, home: Path | None = None
) -> Path | None:
    """The file `--continue` or `--resume <id>` means, or None.

    A bare `--continue` takes the newest. An id is matched as a PREFIX, so a
    user can type the date and skip the random suffix that exists only to stop
    two sessions colliding — an id nobody can retype is an id nobody uses.
    """
    saved = list_sessions(repo_root, home)
    if not saved:
        return None
    if session_id is None:
        return saved[0].path
    for info in saved:
        if info.session_id.startswith(session_id):
            return info.path
    return None
