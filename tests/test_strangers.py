"""M17's apparatus: the frozen repos and the eval sets read out of them.

None of this can check whether the ANSWERS are right — that needs the repos on
disk and an API key, which is exactly the run this apparatus exists to make
repeatable. What it can check is everything that would waste that run: a case
file that does not parse, a key the runner ignores, a pin that is not a pin.

The failure being prevented is specific. An eval run costs real tokens against
a metered account, and the M16e pass already burned a day's budget discovering
its own criterion was unmeasurable. A YAML typo found by pytest costs nothing;
the same typo found by the runner costs the sitting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from pyrrhon.evals.strangers import STRANGERS, fetch, head_sha

EVALS = Path(__file__).parent.parent / "evals"

# What run_eval reads off a case. A key outside this set is silently ignored
# by the runner, which is the quiet way an assertion stops asserting.
CASE_KEYS = {"question", "expected", "expected_any", "must_not_cite", "history", "max_rounds"}

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def stranger_cases() -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for repo in STRANGERS:
        path = EVALS.parent / repo.eval_file
        for case in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            found.append((repo.name, case))
    return found


def test_every_stranger_names_an_eval_file_that_exists():
    """The table and the files are two halves of one fact, and a table row
    pointing at nothing fails only when somebody has already paid to run it."""
    for repo in STRANGERS:
        assert (EVALS.parent / repo.eval_file).is_file(), repo.eval_file


def test_every_pin_is_a_full_commit_id():
    """A tag can be moved and an abbreviation can become ambiguous. The whole
    reason the table exists is that the answers are true of exactly one tree,
    so the pin has to name exactly one tree."""
    for repo in STRANGERS:
        assert SHA_RE.match(repo.sha), f"{repo.name}: {repo.sha!r} is not a full sha"


def test_the_two_repos_are_not_the_same_language():
    """The Go row of the symbol table has never been exercised by an eval.
    Two Python repos would measure the model twice and the index once."""
    assert len({repo.language for repo in STRANGERS}) == len(STRANGERS)


@pytest.mark.parametrize("name,case", stranger_cases())
def test_a_case_asserts_something_the_runner_reads(name: str, case: dict):
    """Three ways a case can look fine and measure nothing: a stray key the
    runner drops on the floor, a question with no assertion attached, and a
    `must_not_cite` that is neither the "*" wildcard nor a list of paths."""
    unknown = set(case) - CASE_KEYS
    assert not unknown, f"{name}: unknown case keys {unknown}"
    assert case.get("question"), f"{name}: a case with no question"
    assert (
        {"expected", "expected_any", "must_not_cite"} & set(case)
    ), f"{name}: {case['question']!r} asserts nothing"
    forbidden = case.get("must_not_cite")
    if forbidden is not None:
        assert forbidden == "*" or isinstance(forbidden, list), forbidden


@pytest.mark.parametrize("name,case", stranger_cases())
def test_expected_locations_are_repo_relative_with_a_line(name: str, case: dict):
    """`--repo` is what makes a case set portable between the clone location
    on one machine and another. An absolute path in a case would pass on the
    machine it was written on and nowhere else."""
    for key in ("expected", "expected_any"):
        for want in case.get(key) or ():
            assert set(want) == {"file", "line"}, want
            assert not Path(want["file"]).is_absolute(), want
            assert isinstance(want["line"], int) and want["line"] > 0, want


def test_each_set_baits_the_criterion_the_roadmap_says_to_bait_hardest():
    """VISION criterion 3 is "says it doesn't know", and it is the one a
    fixture repo cannot test honestly: three files have nothing plausible to
    fabricate about. A stranger repo does, which is the point of the exercise
    — so a set that lost its baits has lost the half that is hard."""
    for repo in STRANGERS:
        path = EVALS.parent / repo.eval_file
        cases = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        baits = [c for c in cases if c.get("must_not_cite") == "*"]
        assert len(baits) >= 5, f"{repo.name}: only {len(baits)} fabrication baits"


def test_fetch_leaves_a_correct_checkout_alone(tmp_path: Path, monkeypatch):
    """Idempotence, and it is not cosmetic: a script calls fetch before every
    eval run, and a clone per run would put a network round trip in front of a
    measurement that is already expensive.

    Built against a repo made here rather than the network, so the test says
    something about the logic instead of about connectivity.
    """
    import subprocess

    origin = tmp_path / "origin"
    origin.mkdir()
    for args in (
        ["init", "--initial-branch=main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(origin), *args], capture_output=True)
    (origin / "a.txt").write_text("one")
    subprocess.run(["git", "-C", str(origin), "add", "a.txt"], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-m", "one"], capture_output=True)
    sha = head_sha(origin)
    assert sha

    from dataclasses import replace

    repo = replace(STRANGERS[0], name="local", url=str(origin), sha=sha)
    root = tmp_path / "strangers"
    assert fetch(repo, root) == root / "local"

    # A second call must not clone again. Breaking the URL is how that is
    # proved: a fetch that still succeeds cannot have gone to the network.
    assert fetch(replace(repo, url="file:///nowhere"), root) == root / "local"
    assert head_sha(root / "local") == sha
