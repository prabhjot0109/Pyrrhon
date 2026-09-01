"""The two repos neither of us wrote, pinned to a commit that cannot move.

M17's premise is that every number in `CLAUDE.md` was measured against
`tests/fixtures/sample_repo` (three files) or against Pyrrhon itself, where
the author's knowledge contaminates every judgment about whether an answer was
good. VISION's four success criteria are about "a real open-source repo
neither of us wrote", and none of them has ever been run on one.

**Freezing the SHA is what makes the run repeatable, and it is not a detail.**
A case says `httpx/_client.py:971`, so a run against a moving `main` compares
today's model against yesterday's line numbers and reports the difference as a
regression. Every later comparison in this milestone — the M18 prompt eval,
the retrieval work M20 may or may not earn — measures against the baseline
these repos produce, so the repos have to be the same repos.

Two repos rather than one, and one of them not Python, because the symbol
index is table-driven across five languages and only the Python row has ever
been exercised by an eval. A Go repo is the cheapest way to find out whether
"multi-language" means anything.

Sizes are the point too. `sample_repo` has three files, which is smaller than
a single module of either of these, and a three-file repo cannot produce the
failure the tool policy exists to prevent: there is nothing to grind through.

Usage:

    uv run python -m pyrrhon.evals.strangers            # fetch and verify
    uv run python -m pyrrhon.evals.strangers --where    # print the paths only

Then run the eval sets against what it fetched, which it prints.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Where clones land. Under the user's Pyrrhon directory rather than in the
# repo: they are ~35k lines of someone else's code, they are derived, and
# nothing about them belongs in `git status`.
DEFAULT_ROOT = Path.home() / ".pyrrhon" / "strangers"


@dataclass(frozen=True)
class StrangerRepo:
    """One frozen repo and the eval file whose answers were read out of it.

    `sha` is a full commit id rather than a tag: a tag can be moved, and the
    whole reason this table exists is that the answers below are only true of
    one tree.
    """

    name: str
    url: str
    sha: str
    language: str
    eval_file: str
    why: str


STRANGERS: tuple[StrangerRepo, ...] = (
    StrangerRepo(
        name="httpx",
        url="https://github.com/encode/httpx.git",
        sha="b5addb64f0161ff6bfe94c124ef76f6a1fba5254",
        language="python",
        eval_file="evals/strangers/httpx.yaml",
        why=(
            "Mid-size Python, ~18k lines, with a shape that produces real "
            "questions: a sync and an async client that mirror each other, a "
            "transport layer that delegates most of the hard work to another "
            "package, and a URL parser worth a question of its own."
        ),
    ),
    StrangerRepo(
        name="cobra",
        url="https://github.com/spf13/cobra.git",
        sha="adbc8813901bba65827259daa8e22ff94ec1f30e",
        language="go",
        eval_file="evals/strangers/cobra.yaml",
        why=(
            "Go, ~17k lines, and the first eval anything has ever run against "
            "a non-Python row of the symbol table. Flat package layout, so a "
            "question is answered by finding the right function rather than "
            "the right directory — which is the harder half for lexical search."
        ),
    ),
)


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, check=False
    )


def head_sha(path: Path) -> str | None:
    """The commit a clone is currently on, or None if it is not a clone."""
    done = _run("git", "-C", str(path), "rev-parse", "HEAD")
    return done.stdout.strip() if done.returncode == 0 else None


def fetch(repo: StrangerRepo, root: Path = DEFAULT_ROOT) -> Path:
    """Put `repo` on disk at exactly its pinned commit, and return where.

    Idempotent by design rather than by accident: run it against a checkout
    that is already correct and it does nothing at all, so a script can call
    it before every eval run without paying for a clone each time. A checkout
    that has drifted — someone pulled, someone experimented — is moved back
    rather than left, because a drifted tree fails the eval as though the
    model had regressed.
    """
    destination = root / repo.name
    if head_sha(destination) == repo.sha:
        return destination
    root.mkdir(parents=True, exist_ok=True)
    if not (destination / ".git").is_dir():
        clone = _run("git", "clone", "--quiet", repo.url, str(destination))
        if clone.returncode != 0:
            raise RuntimeError(f"clone of {repo.name} failed: {clone.stderr.strip()}")
    # Fetch first: a shallow or stale clone may simply not have the commit yet,
    # and `checkout` of an unknown sha reports a confusing "pathspec" error.
    _run("git", "fetch", "--quiet", "origin", cwd=destination)
    checkout = _run("git", "checkout", "--quiet", "--force", repo.sha, cwd=destination)
    if checkout.returncode != 0:
        raise RuntimeError(
            f"checkout of {repo.name}@{repo.sha[:8]} failed: {checkout.stderr.strip()}"
        )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pyrrhon.evals.strangers",
        description="Fetch M17's frozen stranger repos and print how to run their evals.",
    )
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help=f"Where to clone (default {DEFAULT_ROOT})"
    )
    parser.add_argument(
        "--where",
        action="store_true",
        help="Print paths and pinned commits without fetching anything",
    )
    args = parser.parse_args(argv)

    for repo in STRANGERS:
        destination = args.root / repo.name
        if not args.where:
            try:
                destination = fetch(repo, args.root)
            except RuntimeError as exc:
                print(f"{repo.name}: {exc}", file=sys.stderr)
                return 1
        state = head_sha(destination)
        mark = "ok" if state == repo.sha else f"NOT AT PIN (at {state or 'nothing'})"
        print(f"{repo.name} [{repo.language}] {repo.sha[:8]} {mark}")
        print(f"  {destination}")
        print(
            f"  uv run python -m pyrrhon.evals.grounding {repo.eval_file} "
            f"--repo {destination}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
