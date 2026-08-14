# Pyrrhon M11–M14 — trust boundary, correctness, truthful grounding, code intelligence

> Design spec. Written 2026-08-13 after a full-codebase review of `f9da808`
> (460 tests green). Four milestones, executed in order. Each is independently
> shippable; each consumes the previous one's interfaces.

## Context

M0–M10 delivered a working two-act agent: a headless core, three channels, a
grounding gate, nine LLM providers, plugins, MCP, and a measured latency
harness. The review that produced this spec found the codebase strong on craft
(comments carry trade-offs and measurements; `tests/test_safety.py` is a real
fence) and weak in exactly one dimension: **the trust boundary was reasoned
about per-module and never end-to-end.**

That produced one critical defect and a cluster of smaller ones:

| # | Defect | Evidence |
|---|---|---|
| 1 | A cloned repo's `.pyrrhon.toml` can declare `[mcp_servers.x] command=…`, which is spawned at startup with no consent | `settings.py:150` → `repl.py:299` / `tui/app.py:275` → `mcp/manager.py:120` |
| 2 | The same file can declare `[providers.x] base_url` + `[fast] provider="x"`, sending the user's API key to an attacker | same path, `llm.py:204-213` |
| 3 | `<repo>/.pyrrhon/*.md` is globbed into the **system prompt** unconditionally | `soul.py:52-55` |
| 4 | Settings merge is shallow, so a repo `[voice]` table silently deletes the global one | `settings.py:152` |
| 5 | `/settings llm deep …` writes `agent.deep_llm`, an attribute nothing reads — the switch is a no-op and the status bar always lies | `settings_cmd.py:89`, `builtin.py:51`, `loop.py:216-219`, `tui/app.py:162` |
| 6 | A mid-stream provider failure leaves the partial answer in history *and* appends a second assistant message | `loop.py:320-352`, `loop.py:596-600` |
| 7 | Every natural acceptance of Pyrrhon's own offer ("yes please", "yeah do that") has the tool belt withheld | `turn_type.py:79-86` vs `prompts.py:76-79` |
| 8 | `grep` can report `ERROR: grep failed.` for a search that succeeded (returncode read after an unconditional kill) | `repo.py:295-309` |
| 9 | `web_fetch` has no SSRF guard and no pre-read size cap | `web.py:76-94` |
| 10 | `credentials.toml` is created at umask default, then chmod'd | `credentials.py:38-42` |
| 11 | `/mode` appends a system message per switch; `maybe_summarize` deliberately preserves system messages, so they accumulate forever | `session.py:86`, `context.py:153` |
| 12 | Piper HTTP mode leaks an `aiohttp.ClientSession` per `/voice on` | `providers.py:211` |
| 13 | `compaction_ms` telemetry is never recorded; `ScreenArtifact` is never emitted | `telemetry.py:174`, `events.py:20` |

Defects 1–4 all live in the `load_settings` / prompt-assembly path and are
fixed together in M11 — the shallow merge (4) disappears as a side effect of
rewriting that function to partition by provenance, so splitting it out would
mean touching the same code twice. Defects 5–13 are independent and are swept
in M12.

Separately, the deepest **product** risk is not a defect at all: the gate proves
a cited line is *in range*, never that it is the *right* line, and never that
the model actually looked at it. `repo_map` hands the model a list of real
paths and line numbers, so a plausible fabricated citation passes every
mechanical check. `VISION.md:118-120` asks for a *correct* `file:line`; nothing
measures correctness. M13 closes that.

Finally, M10's own postscript lists what Stage 3 still owes. M14 delivers it.

## Non-goals

Carried forward from `VISION.md`'s parked list and reaffirmed here, so they do
not creep into these four milestones: plugin marketplace, enterprise
onboarding, student/interview positioning, company-standards enforcement,
architecture knowledge graph, diagram generation, multi-agent orchestration,
Gemini Live speech-to-speech (bypasses the gate). Nothing in M11–M14 is new
product surface — this is hardening plus the code intelligence already on
record.

---

## M11 — Trust boundary

**Goal:** a repo Pyrrhon has never seen cannot execute code, redirect an API
key, or write the system prompt, without one explicit, content-bound consent.

### The provenance split

`load_settings` stops returning one flat, uniformly-trusted `Settings`. Repo
config is partitioned by what a key can *do*:

```
PRIVILEGED  = {"mcp_servers", "providers", "voice.tts_url"}  # runs code / redirects egress
CONDITIONAL = {"fast", "deep", "fallbacks"}                  # safe unless they name a repo-defined provider
PLAIN       = {"voice.*", "model", "context"}                # always honoured from the repo
```

- **PLAIN** merges from the repo unconditionally. The rest of `[voice]` selects
  among builtin providers whose `base_url` the repo cannot change, so it
  carries no egress risk once `providers` is quarantined.
- **PRIVILEGED** is quarantined into `Settings.pending_grants` and has no
  effect until granted. `voice.tts_url` is in this set despite living in an
  otherwise-plain table: Piper HTTP mode POSTs the text Pyrrhon is about to
  speak to that URL (`providers.py:210`), so a repo that sets it exfiltrates
  the conversation. It is the one field-level exception, and the partition is
  therefore keyed on dotted paths rather than top-level table names.
- **CONDITIONAL** applies only when every provider it names resolves to a
  builtin or a *global* provider. A repo may suggest `groq/llama-3.3`; it may
  not point a slot at a provider it defined itself.

`Settings` gains `pending_grants: list[Grant]` and nothing downstream changes
shape — `settings.mcp_servers` simply never contains an ungranted server, so
`MCPManager` needs no modification. That is deliberate: the fix should not be
enforceable-by-remembering in every consumer.

### Grants are bound to content, not to names

This is the security-critical detail. A grant records
`sha256(canonical_json(value))`. If the user approves

```toml
[mcp_servers.indexer]
command = "node"
args = ["./scripts/mcp-indexer.js"]
```

and the repo later changes `args` to something else, the hash no longer
matches and consent is requested again. Name-only trust would let a repo
launder a malicious payload through an already-approved name — which is the
whole attack, one commit later.

### Storage

`<repo>/.pyrrhon/trusted` is reused and given a line grammar, keeping the
existing bare-name form readable so no user's file breaks:

```
hello-reviewer                                   # legacy: plugin name (still honoured)
config:mcp_servers.indexer=<sha256>
config:providers.internal=<sha256>
soul:.pyrrhon/team-context.md=<sha256>
```

### Soul files

Repo-level `.pyrrhon/*.md` becomes a granted resource on the same mechanism.
Two files are exempt in practice rather than by rule: `/init` records a grant
for `soul.md` when it writes it, and `RememberTool` records one for
`memory.md` on first write. The user authored those, so consent is implicit and
the prompt only ever appears for markdown Pyrrhon did not write — i.e. exactly
the files that arrived with the clone.

### Consent UX

One prompt, at the existing plugin-consent site (`load_channel_plugins`,
before the event loop), listing every pending grant with its concrete effect:

```
This repo wants permissions Pyrrhon does not grant by default:
  run a program     mcp server 'indexer'  ->  node ./scripts/mcp-indexer.js
  send prompts to   provider 'internal'   ->  https://llm.corp.internal/v1
  write its prompt  soul file             ->  .pyrrhon/team-context.md
Allow for this repo? [y/N]
```

No TTY (CI, piped stdin) denies and logs one line. `--trust-repo` pre-approves
for automation; it is explicit, documented, and never implied by any other
flag. Denial is never fatal — Pyrrhon runs with the grants it has.

### Folded-in ops slice

Ruff (lint + format check), mypy on `pyrrhon/core/` only, and a GitHub Actions
workflow running `uv sync` + `ruff` + `mypy` + `pytest` on push and PR. This
lands in M11 rather than later because M12–M14 are large sweeps and should be
guarded by CI before they start, not after.

**Exit criteria:** a fixture repo carrying a hostile `.pyrrhon.toml` and a
hostile soul file spawns nothing, redirects nothing, and injects nothing, under
test; granted config still works; CI is green on the branch.

---

## M12 — Correctness sweep

Thirteen independent defects, each with a regression test that fails before the
fix. Three carry design decisions worth stating here.

**`deep_llm` ownership.** `Agent` gains `self.deep_llm` and a `set_deep_llm()`
seam that updates the attribute *and* the live `ThinkDeeperTool`. The commands
call the seam instead of assigning an attribute nothing reads. A test asserts
that after `/settings llm deep …`, the model `think_deeper` actually calls has
changed — the property the current code silently fails.

**Mid-stream failure.** The partial answer *was heard*, so it stays in history
with the existing `…[interrupted]` marker; the error line is emitted as a
`SpeechChunk` **without** creating a second assistant message. `_emit_final`
gains a `record: bool` parameter. This preserves "history records what was
heard" and removes the consecutive-assistant-turns shape that strict endpoints
reject.

**Affirmative resume.** `classify` checks "did my last message ask a question?"
*before* the social match. If it asked and the reply is affirmative, the turn is
a **resume** and gets the full belt; if it asked and the reply is negative or
closing ("no", "thanks"), it stays social. The M10 token saving is preserved
for genuinely social turns and given up precisely where the product needs tools
— which is the trade the current code has backwards.

**Exit criteria:** every defect has a test that failed before the fix; the
suite stays green; `evals/grounding.yaml` shows no latency regression via
`--compare`.

---

## M13 — Truthful grounding

**Goal:** move the gate from "this line exists" to "this line exists *and we
looked at it*", and measure both acts against `VISION.md`'s criteria.

### The evidence ledger

`Agent` keeps a per-turn `EvidenceLedger`. Every tool result is scanned as it
returns, recording observed *ranges*, not points:

| Tool | Evidence recorded |
|---|---|
| `read_file(p, 1, 400)` | range `p:1-400` |
| `grep`, `find_symbol`, `find_references` | exact lines from each hit |
| `git_blame -L a,b` | range `p:a-b` |
| `repo_map`, `glob` | file existence only — **no lines** |

`GroundingGate.check(text, evidence)` then classifies each reference three
ways instead of two:

- verified **and** observed → citation stands
- verified, **not** observed → downgraded to the bare path, hedged with "I
  haven't opened that line this session"
- not verified → stripped, as today

The `repo_map`-only row is the point of the whole design: a file listed in the
repo map is *not* evidence for a line inside it, which is precisely how a
plausible fabrication passes today.

### Risk and rollout

This can produce false downgrades (a model legitimately citing line 37 of a
file it read as lines 1–400 must pass — hence ranges). It ships behind
`[grounding] require_provenance`, is measured against the expanded eval before
the default flips, and the flip is a separate reviewed commit.

### Eval expansion

- Fix `_matches` (`grounding.py:152`): a case listing three expected citations
  currently passes when **one** matches. Add `expected_all` semantics.
- Add a `wrong_line` case class — right file, fabricated line — which passes
  today and must fail after provenance.
- Move the case set off the two-file fixture onto a real vendored repo.
- `evals/design.yaml` for Act 2, measuring `VISION.md` criterion 4
  mechanically: on a questionable premise the turn must emit an `AskUser`
  event and must **not** call `write_spec` on the first turn.

**Exit criteria:** the `wrong_line` class fails without provenance and passes
with it; design-mode criterion 4 is measured rather than asserted; no
regression on the existing citation cases.

---

## M14 — Code intelligence (M10 Stage 3)

Delivers what M10's postscript deferred. Ordered internally so the blocking
item is first.

1. **Table-driven language registry.** `ast_index.py:220` hardcodes `.py`.
   M10's own note says this must become table-driven *in the same change* as
   new grammars, or the grammars never see a file. Extension → grammar →
   def/ref/import queries, one table.
2. **TypeScript/JavaScript + Go** grammars and queries against that table.
3. **`symbol_context`** — one tool call returning definition, references,
   imports, and the source window, replacing the three-round-trip
   `find_symbol` → `find_references` → `read_file` dance. Emits ledger
   evidence in M13's format.
4. **Conversation-personalised repo map** — files mentioned in the live
   conversation are boosted in the ranking.
5. **Orientation brief** — the first thing said about an unfamiliar repo.
   First real emitter of `ScreenArtifact`, which has been a dead event type
   since M0.
6. **Belt slimming** — folding three tools into `symbol_context` cuts schema
   tokens on every tool round.
7. **`evals/understanding.yaml`** — the quality claim, measured.

**Exit criteria:** a TS and a Go repo index and answer correctly; a
"how does X work" question costs one tool round instead of three, shown in the
trace; `evals/understanding.yaml` passes.

---

## Execution order

```
M11 Trust Boundary  ──►  M12 Correctness  ──►  M13 Truthful Grounding  ──►  M14 Code Intelligence
   (+ ops/CI)
```

Sequential, not parallel, and each edge is a real dependency:

- **M11 → M12.** M11 changes the shape of `load_settings` and adds
  `pending_grants`. M12 touches `settings_cmd.py` and `credentials.py`. Doing
  M11 first means M12 is never rebased onto a changed settings API. M11 also
  brings CI, which should guard the sweeps rather than follow them.
- **M12 → M13.** M13 threads an evidence ledger through `Agent.run_turn` and
  the gate call sites. M12 repairs the streaming/error paths in that exact
  function. Threading new state through a loop with a known history-corruption
  bug means debugging both at once.
- **M13 → M14.** M14's new tools must emit ledger evidence, so the ledger has
  to exist and be stable first. Building `symbol_context` before M13 means
  building it twice.

Rough sizing: M11 1–2 days, M12 1–2 days, M13 2–3 days, M14 3–5 days.

## Found but deliberately not planned

Two review findings are real and are **not** in M11–M14. Recorded here so they
are deferred on purpose rather than lost:

1. **`SymbolIndex._sync_ensure_fresh` holds `_db_lock` across the entire walk
   and parse** (`ast_index.py:225-246`). With up to four tools dispatched
   concurrently (`guards.py:22`), an index-using tool blocks behind a cold
   build. It is a latency question, not a correctness one, and M10's rule is
   that latency work starts from a measurement — so this needs a profile on a
   large polyglot repo first, which M14 Task 2 Step 5 produces. Act on it then,
   with the number in hand.

2. **`abort_current_turn` repairs history synchronously after `task.cancel()`**
   (`session.py:210-214`). A task already scheduled to resume can execute
   straight-line code — including `history.append(assistant_tool_message(reply))`
   at `loop.py:390` — before its next suspension point, so `_repair_history`
   can run *before* the append it exists to undo. The window is narrow and no
   field report matches it. Fixing it properly means awaiting the cancelled task
   before repairing, which makes `abort_current_turn` async and changes the
   barge-in path — the one path with the tightest latency budget and the most
   invariants. That deserves its own milestone with a reproduction harness, not
   a task wedged into a sweep.

## Risk register

| Risk | Mitigation |
|---|---|
| The consent prompt trains users to press `y` | One prompt per repo, showing concrete effects (`node ./x.js`), not abstract permission names. Denial is never fatal. |
| Content-hashed grants annoy users on legitimate config edits | Re-prompt shows a diff of what changed, not a bare re-ask. |
| Provenance gate produces false downgrades and makes Pyrrhon sound unsure | Range-based evidence, off by default until the eval says otherwise, separate commit to flip. |
| Multi-language indexing balloons M14 | Registry first; TS/JS and Go only; anything else is a later milestone. |
| `--trust-repo` becomes the default in someone's shell alias | Documented as automation-only; logged loudly on every use. |
