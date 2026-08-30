"""Oversized tool results: persisted, pointed at, and readable on demand."""

from __future__ import annotations

import pytest

from pyrrhon.core.agent.guards import ToolGuard
from pyrrhon.core.grounding.evidence import EvidenceLedger
from pyrrhon.core.tools.results import ReadResultTool, ResultStore, attribute


@pytest.fixture
def store(tmp_path):
    return ResultStore(tmp_path, page_chars=100, max_chars=10_000)


def _big(n: int) -> str:
    return "".join(f"line {i:04d}\n" for i in range(n))


# -- the store ---------------------------------------------------------------


async def test_a_result_under_the_cap_is_never_written(store, tmp_path):
    guard = ToolGuard(max_result_chars=1000, store=store)
    small = "just a little output"
    assert await guard.clip(small) == small
    assert not (tmp_path / ".pyrrhon" / "results").exists()


async def test_an_oversized_result_keeps_its_head_and_gains_a_pointer(store):
    guard = ToolGuard(max_result_chars=200, store=store)
    whole = _big(200)
    clipped = await guard.clip(whole)
    assert clipped.startswith(whole[:200])
    assert "read_result" in clipped
    assert "r1" in clipped
    assert str(len(whole)) in clipped  # the total, so the model can judge the tail


async def test_the_tail_is_retrievable_in_order(store):
    guard = ToolGuard(max_result_chars=200, store=store)
    whole = _big(200)
    await guard.clip(whole)
    window = await store.page("r1", 200)
    assert whole[200:300] in window
    assert "300" in window  # the next offset to ask for


async def test_the_last_page_says_so_instead_of_pointing_on(store):
    guard = ToolGuard(max_result_chars=10, store=store)
    await guard.clip("x" * 260)
    assert "read_result" in await store.page("r1", 0)
    end = await store.page("r1", 200)
    assert "read_result" not in end
    assert "end of" in end.lower()


async def test_an_unknown_id_never_touches_the_filesystem(store):
    assert (await store.page("../../etc/passwd", 0)).startswith("ERROR:")
    assert (await store.page("r99", 0)).startswith("ERROR:")


async def test_an_offset_past_the_end_is_an_error_not_an_empty_window(store):
    guard = ToolGuard(max_result_chars=10, store=store)
    await guard.clip("x" * 60)
    assert (await store.page("r1", 9999)).startswith("ERROR:")


async def test_the_store_remembers_which_call_produced_each_result(store):
    guard = ToolGuard(max_result_chars=10, store=store)
    await guard.clip("x" * 60, name="grep", args={"pattern": "def "})
    assert store.origin("r1") == ("grep", {"pattern": "def "})
    assert store.origin("r2") is None


async def test_at_the_aggregate_cap_it_truncates_exactly_as_before(tmp_path):
    small = ResultStore(tmp_path, page_chars=100, max_chars=120)
    guard = ToolGuard(max_result_chars=50, store=small)
    assert "read_result" in await guard.clip("a" * 100)
    spilled = await guard.clip("b" * 100)
    assert "read_result" not in spilled
    assert "truncated" in spilled


async def test_the_head_is_what_costs_the_turn_budget(store):
    guard = ToolGuard(max_result_chars=200, store=store)
    clipped = await guard.clip(_big(500))
    assert guard.spent == len(clipped)


async def test_close_removes_every_result_it_wrote(store, tmp_path):
    guard = ToolGuard(max_result_chars=10, store=store)
    await guard.clip("x" * 60)
    written = tmp_path / ".pyrrhon" / "results"
    assert list(written.rglob("*.txt"))
    store.close()
    assert not list(written.rglob("*.txt"))
    # The .gitignore outlives the session on purpose: the next one writes here.
    assert (written / ".gitignore").is_file()


async def test_close_is_idempotent(store):
    store.close()
    store.close()


# -- the tool ----------------------------------------------------------------


async def test_read_result_pages_through_the_store(store):
    guard = ToolGuard(max_result_chars=200, store=store)
    whole = _big(200)
    await guard.clip(whole)
    tool = ReadResultTool(store)
    assert whole[200:300] in await tool.run(result_id="r1", offset=200)


async def test_read_result_resumes_where_the_head_stopped_by_default(store):
    """Offset is optional, and omitting it must not re-deliver the head.

    A model that pays a round to read on and gets back what it already has in
    context has spent the round for nothing.
    """
    guard = ToolGuard(max_result_chars=200, store=store)
    whole = _big(200)
    await guard.clip(whole)
    tool = ReadResultTool(store)
    resumed = await tool.run(result_id="r1")
    assert whole[200:300] in resumed
    assert whole[:100] not in resumed


# -- evidence ----------------------------------------------------------------


class _Call:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


async def test_a_page_is_recorded_as_evidence_for_the_call_that_produced_it(store):
    """Otherwise the model cannot cite lines it was just shown.

    The ledger's read_file branch reads `path` out of the arguments; a page's
    arguments carry a result id instead, so recording it under read_result
    would drop every line in the window.
    """
    guard = ToolGuard(max_result_chars=40, store=store)
    numbered = "".join(f"{n:>5}| line {n}\n" for n in range(1, 40))
    await guard.clip(numbered, name="read_file", args={"path": "pyrrhon/x.py"})

    ledger = EvidenceLedger()
    page = await store.page("r1")
    origin, args = attribute(_Call("read_result", {"result_id": "r1"}), store)
    ledger.record_tool_result(origin, args, page)
    assert origin == "read_file"
    assert ledger.observed("pyrrhon/x.py", 8)


def test_a_call_that_is_not_a_page_is_attributed_to_itself(store):
    call = _Call("grep", {"pattern": "def "})
    assert attribute(call, store) == ("grep", {"pattern": "def "})
    assert attribute(_Call("read_result", {"result_id": "nope"}), store)[0] == "read_result"
    assert attribute(_Call("read_result", {"result_id": "r1"}), None)[0] == "read_result"


def test_a_page_fits_under_the_per_call_cap():
    """Otherwise the model pages through pages.

    A `read_result` window comes back through `ToolGuard.clip` like any other
    tool result, so a page sized AT the per-call cap is persisted itself and
    the pointer at its end names a second stored result. The margin has to
    cover the header and the continuation pointer too, which is why this is
    an inequality with room in it rather than an equality.
    """
    from pyrrhon.core.agent.guards import MAX_TOOL_RESULT_CHARS
    from pyrrhon.core.tools.results import PAGE_CHARS

    assert PAGE_CHARS + 500 <= MAX_TOOL_RESULT_CHARS


async def test_the_store_hides_itself_from_git(store, tmp_path):
    """`.pyrrhon/` is not ignored — memory.md and trusted are meant to be
    committable. Derived session scratch is not, and turning `git status` into
    noise is not a thing to ask the user to fix in their own .gitignore."""
    guard = ToolGuard(max_result_chars=10, store=store)
    await guard.clip("x" * 60)
    assert (tmp_path / ".pyrrhon" / "results" / ".gitignore").read_text() == "*\n"
