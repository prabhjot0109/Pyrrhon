"""What the model was actually shown, per turn.

The gate can prove a cited line EXISTS. It cannot prove the model looked at
it — and repo_map hands the model a list of real paths and line numbers, so a
fabricated citation that lands inside a real file passes every mechanical
check today. This ledger is the missing half.
"""

from pyrrhon.core.grounding.evidence import EvidenceLedger

# ReadFileTool renders f"{n:>5}| {line}" — five-wide right-aligned gutter.
READ_FILE_OUTPUT = """\
    1| def greet(name):
    2|     return f"hello {name}"
    3|
"""

GREP_OUTPUT = 'app.py:5: greet("world")\nutils/helpers.py:1: def greet(name):'

# RepoMapTool renders a bare "path:" header, then indented "  kind name:line".
REPO_MAP_OUTPUT = """\
pyrrhon/core/session.py:
  class Session:36 (12 refs)
  function run_turn:88 (4 refs)
"""


def test_read_file_output_records_the_lines_it_showed():
    ledger = EvidenceLedger()
    ledger.record_tool_result("read_file", {"path": "utils/helpers.py"}, READ_FILE_OUTPUT)
    assert ledger.observed("utils/helpers.py", 1)
    assert ledger.observed("utils/helpers.py", 3)
    assert not ledger.observed("utils/helpers.py", 4)


def test_a_line_inside_a_read_range_counts_as_observed():
    ledger = EvidenceLedger()
    ledger.record_range("pyrrhon/core/agent/loop.py", 1, 400)
    # The model read a 400-line window; citing line 37 of it is legitimate.
    assert ledger.observed("pyrrhon/core/agent/loop.py", 37)
    assert not ledger.observed("pyrrhon/core/agent/loop.py", 401)


def test_grep_output_records_each_hit_line():
    ledger = EvidenceLedger()
    ledger.record_tool_result("grep", {"pattern": "greet"}, GREP_OUTPUT)
    assert ledger.observed("app.py", 5)
    assert ledger.observed("utils/helpers.py", 1)
    assert not ledger.observed("app.py", 6)


def test_the_repo_map_is_evidence_about_files_not_about_lines():
    """The whole point: repo_map proves a file exists and proves nothing about
    any line the model then claims to have read inside it."""
    ledger = EvidenceLedger()
    ledger.record_tool_result("repo_map", {}, REPO_MAP_OUTPUT)
    assert "pyrrhon/core/session.py" in ledger.files
    assert not ledger.observed("pyrrhon/core/session.py", 36)
    assert not ledger.observed("pyrrhon/core/session.py", 999)


def test_glob_output_records_files_only():
    ledger = EvidenceLedger()
    ledger.record_tool_result("glob", {"pattern": "*.py"}, "app.py\nutils/helpers.py")
    assert ledger.files == {"app.py", "utils/helpers.py"}
    assert not ledger.observed("app.py", 1)


def test_git_blame_records_the_range_it_was_asked_for():
    ledger = EvidenceLedger()
    ledger.record_tool_result(
        "git_blame", {"path": "app.py", "start_line": 10, "end_line": 20}, "…blame…"
    )
    assert ledger.observed("app.py", 15)
    assert not ledger.observed("app.py", 21)


def test_git_blame_without_a_range_covers_the_whole_file():
    """`-L` is omitted, so git blamed every line and the model saw all of them."""
    ledger = EvidenceLedger()
    ledger.record_tool_result("git_blame", {"path": "app.py"}, "…blame…")
    assert ledger.observed("app.py", 1)
    assert ledger.observed("app.py", 5000)


def test_windows_style_paths_normalise():
    ledger = EvidenceLedger()
    ledger.record_tool_result("read_file", {"path": "utils\\helpers.py"}, READ_FILE_OUTPUT)
    assert ledger.observed("utils/helpers.py", 2)


def test_an_error_result_records_nothing():
    ledger = EvidenceLedger()
    ledger.record_tool_result("read_file", {"path": "nope.py"}, "ERROR: 'nope.py' does not exist.")
    assert not ledger.observed("nope.py", 1)
    assert ledger.files == set()


def test_a_malformed_result_contributes_nothing_rather_than_raising():
    """Fails closed. A tool that returns something unparseable licenses no
    citation, which is the safe direction — the alternative is a crash on the
    speech path."""
    ledger = EvidenceLedger()
    ledger.record_tool_result("git_blame", {"path": "a.py", "start_line": "oops"}, "x")
    ledger.record_tool_result("read_file", None, READ_FILE_OUTPUT)  # type: ignore[arg-type]
    ledger.record_tool_result("grep", {}, None)  # type: ignore[arg-type]
    assert not ledger.observed("a.py", 1)


def test_a_backwards_range_is_still_a_range():
    ledger = EvidenceLedger()
    ledger.record_range("a.py", 20, 10)
    assert ledger.observed("a.py", 15)


def test_ranges_accumulate_across_tool_calls():
    """A turn that reads two windows of the same file has seen both."""
    ledger = EvidenceLedger()
    ledger.record_range("a.py", 1, 10)
    ledger.record_range("a.py", 90, 100)
    assert ledger.observed("a.py", 5)
    assert ledger.observed("a.py", 95)
    assert not ledger.observed("a.py", 50)
