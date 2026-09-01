# Stranger repos

M17's premise, stated plainly: every number in `CLAUDE.md` was measured
against `tests/fixtures/sample_repo` — three files — or against Pyrrhon
itself, where the author's knowledge contaminates every judgment about whether
an answer was good. VISION's four success criteria are about "a real
open-source repo neither of us wrote", and until this directory none of them
had ever been run on one.

## The two repos

| Repo | Pin | Language | Lines | Why this one |
|---|---|---|---|---|
| [`encode/httpx`](https://github.com/encode/httpx) | `b5addb64` | Python | ~18k | A sync and an async client mirroring each other, a transport layer that delegates the hard parts to another package, and a URL parser worth its own question. |
| [`spf13/cobra`](https://github.com/spf13/cobra) | `adbc8813` | Go | ~17k | The first eval ever to run against a non-Python row of the symbol table. One flat package of very large files, so a question is answered by finding the right *function*, never the right directory. |

`pyrrhon/evals/strangers.py` holds the table and puts both on disk at their
pins:

```bash
uv run python -m pyrrhon.evals.strangers          # fetch, verify, print how to run
uv run python -m pyrrhon.evals.strangers --where  # paths only, no network
```

It is idempotent, so a script can call it before every run, and it forces a
drifted checkout back to the pin rather than leaving it — a drifted tree fails
the eval as though the model had regressed.

## Why the SHA is frozen

A case says `httpx/_client.py:971`. Run it against a moving `main` and you are
comparing today's model against yesterday's line numbers, and reporting the
difference as a regression. Every later comparison in the roadmap — M18's
prompt eval, whatever M20's retrieval work turns out to earn — measures
against the baseline these two produce, so the repos have to be the same
repos.

## How the answers were derived, and why that is the whole point

Every line number in both files was read out of the frozen tree with `grep` on
2026-09-01. **Not one of them came from asking Pyrrhon.** A set built by
recording what the agent said measures only whether the agent is consistent
with itself, which it always is; the distinction is the entire validity of the
exercise, and it is the one part of this that cannot be automated later.

`tests/test_strangers.py` checks everything that would waste a paid run — a
file that does not parse, a case key the runner silently ignores, an absolute
path, a pin that is not a full commit id. It cannot check whether an answer is
*right*; that needs the repos and a key.

## What these sets cannot measure

The runner scores citations, not prose. So a question whose honest answer is
"httpx does not implement this — it hands the count to httpcore at
`_transports/default.py:165`" is unscoreable, because the right answer and a
confident wrong one cite the same line. Those questions are deliberately
absent rather than present and meaningless.

The same limit shapes the fabrication baits. Each names something the repo
genuinely does not contain, verified by grep at the pin, so that *any*
citation is a fabrication rather than a near miss. `yaml` and `plugin` are not
baited against cobra, and response caching is not baited against httpx, for
exactly this reason: in each case one real line carries the word, and a model
that found it would be scored as fabricating when it had in fact found the
only evidence there is.

## The measurement problem this set is meant to solve

M16e's S2S criterion — "the gate's intervention rate falls substantially with
citation accuracy held" — presumed a non-zero starting rate. It was 0.0% in
both arms, because `gpt-oss-120b` declined every fabrication bait correctly on
a three-file repo where nothing plausible existed to invent.

The restated criterion is in the roadmap. Its first requirement is a baseline
that is not zero, and that is what these five-bait sets on real repos exist to
produce. If no available model fabricates even here, that is a finding rather
than a failed run, and it argues the gate is cheap insurance rather than a
load-bearing component — which unblocks S2S on different grounds than the
plan expected.

## Running them

```bash
uv run python -m pyrrhon.evals.strangers
uv run python -m pyrrhon.evals.grounding evals/strangers/httpx.yaml \
  --repo ~/.pyrrhon/strangers/httpx
uv run python -m pyrrhon.evals.grounding evals/strangers/cobra.yaml \
  --repo ~/.pyrrhon/strangers/cobra
```

Read `stop_reason` before reading any score. The M16e pass recorded a run that
looked like a 3/6 regression and was six turns of `stop_reason=error` from a
spent token allowance, which the score alone hid completely.
