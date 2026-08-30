"""Oversized tool results: kept out of context, not thrown away.

`ToolGuard.clip` used to cut a result at the per-call cap and drop the rest on
the floor. The model then had two options, both bad: answer from a head it
knows is partial, or re-run the tool with narrower arguments it has to guess
blind — a full round plus the tool's own latency, to recover bytes that had
already been read once.

So the tail is written to a file and the head carries a pointer to it. Context
costs exactly what it cost before; the tail costs a `read_result` call only if
the model decides it wants it, which is the decision it could not previously
make because it could not see how much it was missing.

The store is derived, session-scoped and deleted on `close()`. Deliberately a
directory of plain files rather than a database: nothing here outlives the
session, so durability, indexing and migration are all cost without a buyer.

Two things about the write path are load-bearing.

Every path is built from the store's own counter (`r1`, `r2`, …), never from
model input. `page` resolves an id through the in-memory index first, so an id
the store did not issue is refused before any path is constructed — which is
why "../../etc/passwd" is an unknown id rather than a traversal.

The aggregate cap degrades to truncation. That is today's behaviour, so the
worst case is no worse than now: a session that dumps four megabytes of tool
output stops persisting and starts cutting, and the only thing it loses is a
capability it did not have last week.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from pyrrhon.core.tools.base import Tool

PAGE_CHARS = 8_000          # one window, sized like the per-call cap
MAX_STORE_CHARS = 4_000_000  # aggregate, per session
STALE_AFTER_SEC = 24 * 60 * 60


@dataclass(frozen=True)
class ResultRef:
    """One persisted result, as the turn needs to see it."""

    id: str
    total: int       # characters in the whole result
    head_end: int    # where the head stopped, i.e. where reading on resumes
    head: str        # exactly what goes into context, pointer included


class ResultStore:
    def __init__(
        self,
        repo_root: Path,
        page_chars: int = PAGE_CHARS,
        max_chars: int = MAX_STORE_CHARS,
    ) -> None:
        self._root = Path(repo_root) / ".pyrrhon" / "results"
        self._dir = self._root / f"{os.getpid()}-{int(time.time())}"
        self.page_chars = page_chars
        self.max_chars = max_chars
        self._refs: dict[str, ResultRef] = {}
        self._origins: dict[str, tuple[str, dict]] = {}
        self._spent = 0
        self._swept = False

    # -- writing -------------------------------------------------------------

    async def persist(
        self, result: str, head_chars: int, name: str = "", args: dict | None = None
    ) -> ResultRef | None:
        """Write `result`, or None when the session's aggregate cap is spent."""
        if self._spent + len(result) > self.max_chars:
            return None
        ref_id = f"r{len(self._refs) + 1}"
        await asyncio.to_thread(self._write, ref_id, result)
        self._spent += len(result)
        head = result[:head_chars]
        ref = ResultRef(
            id=ref_id,
            total=len(result),
            head_end=len(head),
            head=head + self._pointer(ref_id, len(head), len(result)),
        )
        self._refs[ref_id] = ref
        self._origins[ref_id] = (name, dict(args or {}))
        return ref

    def _write(self, ref_id: str, result: str) -> None:
        if not self._swept:
            self._sweep()
            self._swept = True
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{ref_id}.txt").write_text(result, encoding="utf-8")

    def _sweep(self) -> None:
        """Drop stale sibling stores.

        Nothing in either channel closes a session, so `close()` is a courtesy
        the crash path never pays. An age check rather than a liveness one:
        a store older than a day belongs to no running process worth guessing
        about, and a store younger than that may be a second Pyrrhon on the
        same repo right now.
        """
        cutoff = time.time() - STALE_AFTER_SEC
        try:
            siblings = list(self._root.iterdir())
        except OSError:
            return
        for sibling in siblings:
            try:
                if sibling.is_dir() and sibling.stat().st_mtime < cutoff:
                    shutil.rmtree(sibling, ignore_errors=True)
            except OSError:
                continue  # a sibling we cannot stat is a sibling we leave alone

    def close(self) -> None:
        """Idempotent: the session may end more than one way."""
        shutil.rmtree(self._dir, ignore_errors=True)

    # -- reading -------------------------------------------------------------

    def origin(self, ref_id: str) -> tuple[str, dict] | None:
        """The call that produced this result, for whoever needs to attribute
        a page back to it. The grounding ledger does."""
        return self._origins.get(ref_id)

    async def page(self, ref_id: str, offset: int | None = None) -> str:
        ref = self._refs.get(ref_id)
        if ref is None:
            return (
                f"ERROR: no stored result '{ref_id}'. The id comes from the "
                f"pointer at the end of a truncated result."
            )
        start = ref.head_end if offset is None else max(0, int(offset))
        if start >= ref.total:
            return (
                f"ERROR: offset {start} is past the end of result {ref_id} "
                f"({ref.total} characters)."
            )
        window = await asyncio.to_thread(self._read, ref_id, start)
        end = start + len(window)
        name, _ = self._origins.get(ref_id, ("", {}))
        header = f"[result {ref_id} from {name or 'a tool'}, characters {start}-{end} of {ref.total}]"
        if end >= ref.total:
            return f"{header}\n{window}\n[end of result {ref_id}]"
        return f"{header}\n{window}\n{self._pointer(ref_id, end, ref.total)}"

    def _read(self, ref_id: str, start: int) -> str:
        text = (self._dir / f"{ref_id}.txt").read_text(encoding="utf-8")
        return text[start : start + self.page_chars]

    def _pointer(self, ref_id: str, seen: int, total: int) -> str:
        return (
            f"\n…[truncated: {total - seen} of {total} characters remain. "
            f'Call read_result with result_id="{ref_id}", offset={seen} to read on.]'
        )


def attribute(call, store: ResultStore | None) -> tuple[str, dict]:
    """Which call a tool result should be recorded as evidence FOR.

    A `read_result` page is the tail of some OTHER call, and the ledger's
    branches are keyed by tool name: the read_file branch needs `path` out of
    the arguments to turn a numbered gutter into an observed range, and
    read_result's arguments carry an id instead. Recording a page under
    read_result would therefore silently drop every line in it, so the model
    that paged through a file could not cite what it had just been shown.

    Lives here rather than in the loop because the mapping is the store's
    knowledge. The loop asks; it does not know how the answer is derived.
    """
    if call.name != "read_result" or store is None:
        return call.name, call.arguments
    ref_id = call.arguments.get("result_id") if isinstance(call.arguments, dict) else None
    return store.origin(str(ref_id)) or (call.name, call.arguments)


class ReadResultTool(Tool):
    name = "read_result"
    # Terse on purpose: the belt's schema rides on every tool-bearing turn
    # and its size is capped (tests/test_safety.py). It can afford to be,
    # because the pointer at the end of a cut-short result teaches the call.
    description = (
        "Read on from a tool result that was cut short. Pass the result_id "
        "from its last line; omit offset to continue where it stopped."
    )
    parameters = {
        "type": "object",
        "properties": {
            "result_id": {
                "type": "string",
                "description": "The id in a cut-short result's last line, e.g. 'r1'",
            },
            "offset": {
                "type": "integer",
                "description": "Character to resume from; omit to continue.",
            },
        },
        "required": ["result_id"],
    }

    def __init__(self, store: ResultStore) -> None:
        self.store = store

    async def run(self, result_id: str, offset: int | None = None) -> str:
        return await self.store.page(result_id, offset)
