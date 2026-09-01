# Evals

Pyrrhon asserts two things about itself that are easy to believe and hard to
verify: that it cites real locations (Act 1) and that it argues with you before
it documents your design (Act 2). These are the files that measure them instead.

Every runner here needs a real LLM and therefore an API key — `GROQ_API_KEY`,
or whatever provider `.pyrrhon.toml` selects. They are not part of `pytest`;
the suite tests the *runners*, and the runners test the *agent*.

## The files

| File | Act | Repo it runs against |
|---|---|---|
| `grounding.yaml` | 1 — Understand | `tests/fixtures/sample_repo` (default) |
| `grounding-self.yaml` | 1 — Understand | Pyrrhon itself (`--repo .`, required) |
| `understanding.yaml` | 1 — Understand | Pyrrhon itself (`--repo .`, required) |
| `strangers/httpx.yaml` | 1 — Understand | encode/httpx at `b5addb64` |
| `strangers/cobra.yaml` | 1 — Understand | spf13/cobra at `adbc8813` |
| `design.yaml` | 2 — Design | any (`--repo .`) |

A file per repo, not one big file, because a case is only meaningful against
the repo its answers came from. Run the fixture set with `--repo .` and every
expected path is missing; run the self set against the fixture and the same is
true. Keeping them separate makes each file runnable rather than half-failing.

`strangers/` is M17's answer to the fact that everything above it was measured
either on three files or on the codebase whose author is doing the judging. See
`strangers/README.md`; `uv run python -m pyrrhon.evals.strangers` puts both
repos on disk at their pinned commits and prints how to run them.

## Running them

```bash
# Act 1, fixture repo — the fast smoke test.
uv run python -m pyrrhon.evals.grounding evals/grounding.yaml

# Act 1, real repo — the one that can actually catch a fabrication.
uv run python -m pyrrhon.evals.grounding evals/grounding-self.yaml --repo .

# Act 2.
uv run python -m pyrrhon.evals.design evals/design.yaml --repo .
```

Both grounding runs exit non-zero if any case fails, so they work in CI.

### Latency

The grounding runner doubles as the latency harness — it collects a `TurnTrace`
per case, which is where M10's stage-by-stage numbers came from.

```bash
# Record a baseline (5 passes, for variance).
uv run python -m pyrrhon.evals.grounding evals/grounding-self.yaml \
  --repo . --repeat 5 --json baseline.json

# Later: fail if a median regressed more than 1.2x.
uv run python -m pyrrhon.evals.grounding evals/grounding-self.yaml \
  --repo . --repeat 5 --compare baseline.json
```

`--tolerance` adjusts that 1.2x. `first_speech_ms` is the metric that matters
most: it is what the user actually waits through.

## Grounding case keys

```yaml
- question: "Where is greet defined?"
  expected:                                  # EVERY entry must be cited
    - {file: utils/helpers.py, line: 1}      #   line within +/-5

- question: "Where are tool calls dispatched?"
  expected_any:                              # at least ONE must be cited
    - {file: pyrrhon/core/agent/guards.py, line: 62}
    - {file: pyrrhon/core/agent/guards.py, line: 104}

- question: "How does the retry backoff work?"
  must_not_cite: "*"                         # no citations at all
- question: "Which line of session.py does the backoff?"
  must_not_cite: ["pyrrhon/core/session.py"] # not these paths
```

A case may combine `expected`, `expected_any` and `must_not_cite`.

**`expected` requires all of them.** Until M13 it passed on the first match, so
a case demanding three citations passed on one. If several locations are
genuinely equally correct, that is what `expected_any` is for — say so
explicitly rather than relying on a loose `expected`.

### The two negative classes are different tests

`must_not_cite: "*"` asks whether Pyrrhon admits ignorance. The fixture cases
use it for things the sample repo has none of, so a model that invents anything
invents a whole path — and the gate strips fabricated paths already.

`must_not_cite: [<real path>]` is the harder one, and it is why
`grounding-self.yaml` exists. It names a real file that contains nothing about
the question. A guessing model cites a plausible in-range line inside it, the
path verifies, the line verifies, and the old gate passed it. That is the case
provenance (`[grounding] require_provenance`) exists to catch.

## Design case keys

```yaml
- premise: "Let's use MongoDB. We have users, orders, and we join them constantly."
  must_challenge: true          # the turn must emit an AskUser question
  must_not_write_spec: true     # the turn must not call write_spec

- premise: "That's settled then. Write it up."
  must_write: PRD.md            # the turn MUST call write_spec
  history:                      # seeds earlier turns — see below
    - {role: user, content: "..."}

- premise: "Add a caching layer in front of the tool belt in this repo."
  must_look: true               # the open repo must be READ before designing
```

This measures VISION.md's fourth success criterion — that Pyrrhon "pushes back
on at least one questionable choice before writing a spec". The check is
mechanical on purpose, the same reasoning as the grounding gate: an LLM judge
would be slower, non-deterministic, and would itself need evaluating. "Asked a
question and did not write a spec" is a crude proxy for Socratic behaviour, but
it is a proxy that cannot drift.

`history` and `must_look` arrived with M21 and each closes a way the set
measured only the easy half.

Without `history`, every case was an opening turn, so the only thing measurable
was refusing to write a spec — and a model that challenges everything and
produces nothing scored full marks. "The reasoning is now established, so write
it up" is by definition not turn one. Seeded messages are appended AFTER
`set_mode`, because `set_mode` injects the base prompt into an empty history
and a history seeded first would sit above the system message rather than below
it.

`must_look` passes when any read tool ran, and `write_spec` does not count:
writing the answer is not the same as reading the constraints. It catches the
expensive failure — a design produced in the abstract while the code it has to
live in sits unopened, which yields a spec that is internally coherent and
impossible to build here. That is worse than an obviously wrong one, because it
looks finished.

## Adding a case

1. **Open the file and confirm the answer.** A case whose expected line was
   guessed is worse than no case: it fails for the wrong reason and teaches you
   to distrust the eval.
2. Put it in the file matching the repo it was derived from.
3. Prefer `expected_any` when you find yourself hesitating between two equally
   correct locations. Do not widen `LINE_TOLERANCE` — that weakens every case
   in every file at once.

Line numbers in `grounding-self.yaml` **drift** as the code moves. When a case
there starts failing, open the file and check whether the answer relocated
before concluding the model got worse.
