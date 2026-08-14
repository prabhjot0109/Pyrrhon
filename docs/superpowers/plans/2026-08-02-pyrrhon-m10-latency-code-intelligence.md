# Pyrrhon M10: low-latency agent harness + code-intelligence rebuild

> **Status (2026-08-02): Stages 0, 1, 2 and 4 are implemented and landed on
> `m10-latency-harness`. Stage 3 (code intelligence) is deferred by decision.**
> See the *Implementation record* at the end for what changed, what was
> measured, and the four places where the plan was wrong.

## Context

Pyrrhon is voice-first, so latency *is* the product. `VISION.md:98-100` says it
outright: "the moat is quality of experience (latency, interruption, grounding),
not access to a secret capability." Today the agent harness fights that goal in
six specific, measurable ways — and none of them are the loop's *shape*, they are
scheduling, caching, and tool-layer decisions inside it.

Measured against `Session.last_turn_latency_ms` (`session.py:71-79`, already
wired to the TUI status bar), a typical turn today looks like:

```
user_text
  → maybe_summarize                        0 or 1 FULL LLM round trip, blocking
  → llm.chat(history, 1543 tok of schemas) 1 RTT — whole reply buffered in text mode
  → tools run ONE AT A TIME                Σ of every tool latency, not max()
  → llm.chat again                         1 RTT
  → GroundingGate.check                    re-reads every cited file, uncached
  → (if unverified) retry llm.chat         +1 RTT
  → first SpeechChunk                      ← FIRST OUTPUT THE USER SEES/HEARS
```

The concrete defects behind that trace:

1. **Text and TUI never stream.** `loop.py:193` gates streaming on
   `self.voice_active`, so every screen turn buffers the entire answer.
2. **Tool calls are strictly sequential.** `loop.py:282-291` is a plain `for`
   loop with `await` inside. Industry measurement puts sequential tool execution
   at 35-61% of total agent request time.
3. **The prompt prefix is never stable, so no provider ever caches it.**
   `loop.py:174` rewrites `history[0]` every turn and `loop.py:186` re-serializes
   ~1543 tokens of tool schemas every round. Prefix caching is worth 30-60% of
   TTFT when the prefix is anchored.
4. **`maybe_summarize` is a blocking pre-flight LLM call** (`loop.py:180`).
5. **`grep` is pure Python** (`repo.py:92-126`) — it reads every file in the repo
   into Python and regexes it line by line, per call, uncached.
6. **The grounding gate re-reads every cited file on every check**
   (`gate.py:79-92`), and on the voice path that is once *per spoken sentence*.

Separately, code intelligence is thinner than the "understands how this depends
on that" bar: `repo_map` is plain cross-file reference counting with no
conversation awareness, the tree-sitter index is **Python-grammar only**, and
answering "how does X work" costs three round trips (`find_symbol` →
`find_references` → `read_file`) that should cost one.

**Intended outcome:** first spoken/printed word in well under a second on a warm
repo, tool rounds that cost `max()` instead of `sum()`, a cacheable prompt
prefix, and a code-intelligence layer that answers dependency questions in one
tool call instead of three — without weakening the grounding gate or the
barge-in invariants, which are non-negotiable.

## Decision: keep Pyrrhon's own harness, steal everyone's ideas

Migrating onto an external harness was evaluated. **Don't.**

| Harness | Why not here |
|---|---|
| **Claude Agent SDK** | Anthropic owns the loop *and* the model. Kills the provider openness named as a requirement (9 LLM providers today). No seam to gate text between token generation and TTS — its guardrails run after the final output. Built for editing with bash/write tools; Pyrrhon is read-only by construction (`tests/test_safety.py`). |
| **Codex / `codex-core`** | 95%+ Rust; the embedding surface is a Rust crate or a Python SDK driving the `codex` binary. Pyrrhon is in-process Python with Pipecat. A subprocess boundary adds IPC on the exact critical path we're shaving, and you lose per-sentence interception entirely. OpenAI-model-centric. |
| **GitHub Copilot harness** | Not an embeddable library — it's a product. Its June 2026 benchmark is worth reading, not adopting. |
| **opencode (SST)** | Genuinely excellent and genuinely open (75+ providers, LSP). Wrong language: TypeScript/Bun, client-server. Pyrrhon would become a Python voice frontend doing an HTTP round trip per event, and the grounding gate would have nowhere to live. |
| **LangGraph** | Pyrrhon's turn is one loop, not a graph. Its value — durable execution, checkpointing, human-in-the-loop interrupts, multi-agent topologies — is value Pyrrhon needs none of; it needs the loop to be *shorter*. Its interrupt model is checkpoint-based, not "cancel the task mid-`await` and repair history," which is what barge-in actually is and what `session.py:114-156` already does correctly. |
| **deepagents** | A planner tool costs a round trip per turn — the opposite of a voice budget. `think_deeper` and file-backed memory (`soul.py` + `memory.md`) already exist. |
| **OpenAI Agents SDK / Pydantic AI** | The closest peers, and both would replace ~420 lines that already work. Their loop shape is essentially `run_turn`'s — which is the point: **they confirm the design rather than improve it.** |

The decisive fact: **the agent loop is not what's slow.** Every measured defect
above is scheduling, caching, or tool-layer — 5-to-50-line changes inside files
that already exist. A migration would pay a full rewrite of the barge-in and
grounding machinery to fix bugs that don't need it, and most options would also
cost provider openness.

What Pyrrhon *is* missing is the tooling and context layer around the loop. So
port the ideas, not the code:

- **Aider** → PageRank repo map, personalized by conversation mentions.
- **Serena / LSP-to-MCP + the 2026 tree-sitter knowledge-graph result** (~10x
  fewer tokens, 2.1x fewer tool calls than grep-and-read) → symbol-neighborhood
  tools that collapse three round trips into one.
- **Copilot's harness benchmark** → fewer, richer tools beats more tools.
- **Codex** → compact-on-overflow (already ported at `loop.py:219-242`).
- **Voice-agent research** → two-track execution: acknowledge immediately,
  investigate silently. "Hide latency behind speech."
- **opencode** → LSP as the *later* multi-language upgrade path. Note it needs
  no harness change at all: Serena already ships as an MCP server, and
  `pyrrhon/core/mcp/manager.py` can mount it today.

## Stage 0 — Telemetry first (0 ms won, but nothing after it is provable)

New `pyrrhon/core/telemetry.py`: a `TurnTrace` of `time.monotonic()` marks —
`preamble_ms`, per-round `llm_ttft_ms` / `llm_total_ms` / `tool_ms: dict[name,
float]` / `gate_ms`, plus turn-level `retry_ms`, `forced_answer_ms`,
`first_speech_ms`, `prompt_chars`, `schema_chars`. `Agent` stamps it, `Session`
exposes `last_turn_trace`.

Two traps: `tests/test_latency.py:37` *assigns* `session.last_turn_latency_ms`,
so it must stay a plain attribute, not become a derived property. And
`tests/test_grounding_eval.py:57` asserts `report == EvalReport(...)`, so any
new `EvalReport` field needs `field(default_factory=list, compare=False)`.

## Stage 1 — Delete the wasted round trips

Everything here is inside `pyrrhon/core/agent/loop.py` and
`pyrrhon/core/providers/llm.py`. No new dependencies.

**1.1 Stream on every channel.** `loop.py:193` currently reads
`streaming = self.voice_active and hasattr(self.llm, "stream")`. Drop the
`voice_active` conjunct — keep the `hasattr` guard, which is what makes this
safe: `tests/helpers.py:FakeLLM` exposes only `chat()`, so the entire existing
suite keeps taking the non-streaming path unchanged.

**Blocker that must land in the same change:** `_pop_sentences` splits on
`(?<=[.!?])\s+`, but `TEXT_STYLE` (`prompts.py:77-79`) explicitly encourages
tables and fenced code blocks. Sentence-splitting a markdown table or a code
fence produces garbage on screen. So `_stream_round` needs a channel-aware
splitter: voice keeps `_pop_sentences`; text gets `_pop_blocks(buffer,
in_fence)` that flushes on a blank line **outside** a ``` fence and never
flushes mid-fence, mid-table (line starts with `|`), or mid-list. Both feed the
same `_gate_sentence` (`loop.py:362-373`), so grounding is unchanged.

Gate the grounding retry on `gated.unverified and self.allow_retry and not
streaming`. Running the `_emit_final` retry on a streaming path is incoherent —
earlier sentences are already on screen, so "un-saying" them violates "history
records what was heard." Real streaming providers therefore never retry; the
non-streaming `FakeLLM` path keeps today's behaviour exactly.

Then teach the channels to render incrementally — `repl.py:327` does one
`console.print(Markdown(...))` per `SpeechChunk`, which already works
block-by-block; the TUI's `_render_event` (`tui/app.py:192-219`) needs
`SpeechChunk` to append to the live message widget rather than add a new one.

**1.2 Cap the reply.** `llm.py:101-105` sends `{"model", "messages", "tools"}`
and nothing else. Add `max_tokens` and `temperature` as constructor args plumbed
from a new `[model]` settings section, with voice-appropriate defaults (voice
turns should be a few sentences — `VOICE_STYLE` asks for that in prose but
nothing enforces it). Unbounded generation in non-streaming mode is directly
time-to-first-visible-output.

**1.3 Get `maybe_summarize` off the critical path.** `loop.py:179-185` awaits a
full LLM round trip *before* the first token whenever history exceeds
`budget_tokens` (32000). Delete that await. Keep the synchronous call only in
the `ContextLengthExceededError` recovery path (`loop.py:219-242`), where it is
unavoidable and already exists as the safety valve. Before round one, only
`compact_tool_results` runs — pure, local, no LLM call.

**Ownership matters here.** Compaction must be owned by `Session`, *not* the
turn task: the turn task is cancelled on barge-in (`session.py:125`), and a
compaction cancelled halfway would be lost. So:

- `Session._compaction: asyncio.Task | None`, scheduled on the *normal
  completion* path of `_run_turn_events` (`session.py:102-106`), never on the
  cancel path.
- **`_run_turn_events` cancels any pending compaction before starting the next
  turn** (see Addendum A1 — this supersedes the original "await it" design).
  `maybe_summarize` splices `history[1:split]` (`context.py:154`) on the same
  list the loop iterates, so the two must never overlap. Cancellation is safe
  because the only await inside `maybe_summarize` is the `llm.chat` call at
  `context.py:142`, which happens *before* any mutation — a cancelled
  compaction leaves history untouched, which `context.py:129-131` already
  documents as the contract.
- At most one outstanding compaction.

**Also raise `budget_tokens` 32000 → 90000** (`settings.py:88`). Most fast
models are 128k; 32k fires compaction far more often than needed. Cheapest line
in the plan. Breaks `tests/test_settings.py:95`, which asserts the old default.

**1.4 Overlap the grounding gate with generation.** `_gate_sentence`
(`loop.py:362-373`) `await`s a `to_thread` hop per sentence, serialised against
consuming the next chunk. Restructure `_stream_round` so gating runs as a task
per sentence and results are emitted in order — the stream keeps draining while
sentence *n* is being verified.

**Expected:** removes 1-2 full round trips from the median turn and makes text
TTFT equal to first-sentence latency instead of whole-answer latency.

## Stage 2 — Parallel tools, stable prefix, fast tool layer

**2.1 Run tool calls concurrently.** `loop.py:282-291` is a plain `for` loop with
`await self._run_tool(...)` inside; `escalate.py:80-89` is the same. Replace with
`asyncio.gather` over the calls. Four invariants must survive, and each has a
specific mechanism:

- *History order* — build the `{"role": "tool", ...}` messages into a
  pre-sized list indexed by call position and extend `history` in call order
  after the gather. Never append from inside a task.
- *ToolGuard accounting* — `is_duplicate` mutates `_seen` and `clip` mutates
  `_spent` (`guards.py:32-46`). Run `is_duplicate` for all calls *serially
  before* dispatching (it's pure bookkeeping, no I/O), and apply `clip` to
  results serially after the gather, so both stay deterministic.
- *Cancellation* — `gather` propagates `CancelledError` to children, so
  `Session.abort_current_turn` (`session.py:114-126`) still kills in-flight
  tools. `_repair_history` already handles the partial-append case, and the
  index-then-extend ordering above means history is either fully extended or
  not at all.
- *Event order* — emit all `ToolCallStarted` before the gather and all
  `ToolCallFinished` in call order after it. This is forced anyway (an async
  generator can't yield from inside a TaskGroup callback) and it's free:
  `ToolCallFinished` has no render branch in either `tui/app.py:192-219` or
  `repl.py:322-333` — only tests observe its ordering.

Two hazards the naive version hits:

- **Executor starvation — the real regression risk.** `read_file`, `grep`,
  `find_symbol` *and* `GroundingGate.check` all share the default `to_thread`
  executor. Six concurrent greps could stall the gate, which sits directly on
  the speech critical path. Wrap dispatch in an `asyncio.Semaphore(4)`,
  commented as "leave executor headroom for the grounding gate."
- **`ExceptionGroup` leakage.** Wrap each dispatch in a `_guarded(name, args)`
  helper that catches `Exception` and returns an `ERROR:` string, mirroring
  `_run_tool`'s existing `TypeError` handling (`loop.py:413-420`). The
  TaskGroup then never sees a child exception, so no `except*` handling is
  needed anywhere — including `escalate.py:96`'s bare `except Exception`.

Worth stating plainly: today's loop *already* runs calls #2..#N after the
budget is blown on #1 (it only checks `exhausted` after the round), so
parallelism changes **when** work happens, never **how much**.

**2.2 Make the prompt prefix cacheable.** Four changes:
- `loop.py:174` rewrites `history[0]["content"]` every turn. Make it a no-op
  when the string is unchanged (compare before assigning) so the prefix is
  byte-stable across turns and providers can hit their prefix cache.
- `loop.py:186` rebuilds `schemas` per turn. Memoize on the agent, invalidated
  when the tool dict changes.
- `maybe_summarize` rewriting `history[1:split]` (`context.py:153-156`) busts
  the cache below the summary point. Accept that (it's rare and it's a win
  anyway), but with 1.3 it now happens *after* the turn, so the next turn pays
  a cold prefix instead of the current one paying a round trip.
- **Cap the soul prompt.** `build_system_prompt`
  (`pyrrhon/core/agent/soul.py:27-32`) concatenates *every* `*.md` under
  `~/.pyrrhon/` and `<repo>/.pyrrhon/`, and `memory.md` holds up to 200 bullets
  (`memory.py:28`). That can reach ~20 KB ≈ 5k tokens re-sent on **every round
  of every turn**. Cap `load_soul` at ~6000 chars, newest-bullets-first for
  `memory.md`, and log once when truncating.

**Deliberately *not* doing:** moving the style block out of `history[0]` into a
floating trailing system message. It would keep the prefix stable across a
`/voice` toggle, but it collides with `maybe_summarize`'s `kept_system` logic
(`context.py:153`), which preserves every system message in the compacted span
and would accumulate stale style blocks. A `/voice` toggle busting the cache
once per toggle is an acceptable price.

**2.3 ripgrep behind the existing safety fence.** `GrepTool`
(`repo.py:92-126`) reads every repo file into Python and regexes it line by
line. Replace the body with an argv-list `subprocess` call to `rg` — the exact
pattern `git.py` already uses (no `shell=True`, no user string reaching a
shell), keeping the current pure-Python scan as the fallback when `rg` is
absent. `rg` is present in this environment (15.2.0) but must not be assumed.

`tests/test_safety.py:70-82` asserts `git.py` is the *only* file in
`pyrrhon/core/tools/` containing `subprocess`. Widen that fence to an explicit
allowlist (`git.py`, `repo.py`) rather than deleting it, and add a test that
the rg argv is a list with `--` before the pattern so a pattern beginning with
`-` can't be read as a flag. The docstring's rule holds: this is the design
discussion it asked for.

While in there, `grep` takes only `pattern` — add `path`/`glob`, `ignore_case`,
and `context_lines`. Every one of those currently forces the model into extra
`read_file` round trips.

**2.4 Cache what's re-read.** `GroundingGate._count_lines` (`gate.py:79-92`)
does a full `read_text()` + `splitlines()` per citation with no cross-call
cache — on the voice path that is once per spoken sentence, and Stage 1.1 makes
it worse by running `check()` per block on the text path too. Cache keyed on
`(st_mtime_ns, st_size)` — **both**, since size closes the truncation hole that
coarse mtime granularity leaves open. Keep `splitlines()` for the count; do not
switch to `count(b"\n")`, because `splitlines()` also splits on `\r`, `\x0b`,
`\x0c` and `\x1c-\x1e` and `\x85`/``/``, and changing that changes
verification semantics. Same for `_sync_build_repo_map` (`ast_index.py:307-343`),
which re-runs a correlated-subquery-per-symbol-row query and rebuilds the string
on every call: memoize on the index generation. And replace `ensure_fresh`'s full
re-walk (`ast_index.py:147-157`, `INDEX_FRESH_TTL_SEC = 2.0` at
`ast_index.py:131`) with a longer TTL plus explicit invalidation, since a
read-only agent rarely races its own repo.

**Expected:** tool rounds cost `max()` not `sum()`; grep drops from
seconds to milliseconds on a large repo; TTFT drops a further 30-60% on cache
hits.

## Stage 3 — Code intelligence worth the name

**3.1 One tool call instead of three.** Answering "how does X work" today costs
`find_symbol` → `find_references` → `read_file` — three serial round trips.
Add a `symbol_context(name)` tool over the existing `symbols`/`refs`/`imports`
tables that returns, in one result: the definition with its source lines, its
callers with `path:line`, what it calls, and the importers of its file. This is
the change behind the reported 2.1x tool-call and ~10x token reductions, and it
directly serves "map how this depends on that."

**3.2 PageRank the repo map.** `_sync_build_repo_map` ranks files by summed
cross-file reference count. Replace with personalized PageRank over the
symbol→reference graph already in SQLite, weighting symbols the user has
mentioned this conversation (Aider weights conversation mentions 10x, in-context
files 50x). No new dependency — it's ~40 lines of power iteration over rows you
already have.

**3.3 Multi-language.** `ast_index.py:28` hardcodes `get_language("python")`.
`tree-sitter-language-pack` (already a dependency) bundles 305+ grammars. Make
the grammar and its three queries a per-language table keyed by file extension,
starting with the languages you'd actually point Pyrrhon at. Today Pyrrhon is
blind to any repo that isn't Python — which is most repos a user would want
explained to them.

Note `_iter_files_with_mtime` (`ast_index.py:205-210`) hardcodes
`entry.name.endswith(".py")` — the extension set must become table-driven in
the same change, or new grammars will never see a file.

**3.4 Orientation brief.** The prompt tells the model to call `repo_map` first
on an unexplored repo (`ast_index.py:429-433`), costing a round trip on turn
one. Instead, inject a small ranked brief into the system prompt once per
session, built from the already-warmed index
(`warm_index_in_background`, `repl.py:52-70`). It's part of the stable cacheable
prefix from 2.2, so it costs nothing per turn.

**3.5 Slim the belt.** 15 tools ≈ 1558 measured tokens of JSON schema on *every
round of every turn*. `write_spec` is design-mode-only by prompt instruction
(`prompts.py:48-50`) — make it design-mode-only by construction. Fold
`find_symbol` + `find_references` into `symbol_context`. This shrinks the
per-round prefix and, per Copilot's harness benchmark, improves tool-selection
accuracy — fewer, richer tools beat more tools.

`tests/test_safety.py:19-26` pins `EXPECTED_BELT` exactly. That's intentional
and correct: update it deliberately as part of this stage, not incidentally.

## Stage 4 — The conversational partner (cheap, lands alongside Stage 1)

**4.1 Turn-type classification with zero round trips.** `SYSTEM_PROMPT`
(`prompts.py:18-33`) already asks the model to classify — but it still burns a
full round with all ~1558 tokens of tools attached just to decide that "hi"
needs no tools. New pure-Python `pyrrhon/core/agent/turn_type.py`:

- `SOCIAL` — anchored exact-match over a small whitelist
  (`^(hi|hey|hello|thanks|yes|ok|go on|keep going|sounds good)[.!]?$`), <4 words
  → pass `tools=None`.
- `AMBIGUOUS_FOLLOWUP` — short, no repo nouns, previous assistant message ended
  in a question → `tools=None`.
- `REPO_QUESTION` — everything else → full belt.

The whitelist is anchored and exact, so *"hi, where is the auth middleware"*
does not match `^hi$`. On the 25-40% of voice turns that are acknowledgements
this removes ~1558 tokens *and* eliminates any chance of a spurious tool round.

**4.2 Make the filler stop sounding canned.** `bridge.py:67-72` speaks a fixed
line at 1.6 s. The bridge already sees `ToolCallStarted` (`bridge.py:184`) —
template the filler from the most recent tool *name* ("searching the repo for
that…", "reading the file now…"). No LLM call, no added latency. Lower
`FILLER_DELAY_SEC` to 1.2 s, since with streaming on every channel the model's
own narration usually beats it.

**Grounding flag:** the filler bypasses the gate entirely (`bridge.py:207`
pushes a raw `SpeechChunk`). So tool-derived filler must be citation-free *by
construction* — fixed template plus the tool name, **never the args**. Say
"reading the agent loop file," never "loop.py line 193."

**4.3 `AskUser` in understand mode.** It's emitted only when `mode == "design"`
(`loop.py:257, 330, 357`), but `extract_question` (`loop.py:95-106`) is
mode-agnostic and `VOICE_STYLE:64-68` already tells the model to end most turns
by offering the next thread — those offers *are* questions. Emit `AskUser`
whenever the final text ends in a question, in both modes; mode only changes
styling. Plus three lines in `SYSTEM_PROMPT` for the actual skeptic behaviour:
when the user asserts something that contradicts tool output, say so and cite;
when a question presumes a design that isn't in the repo, challenge the premise
before answering.

This breaks `tests/test_extract_question.py:59-62`
(`test_understand_mode_never_yields_askuser`) **by design** — it asserts exactly
the behaviour being changed.

## Changes that need explicit sign-off

`tests/test_safety.py:6-8` says: *"If a change breaks one of these, that change
needs a design discussion, not a test edit."* Three items qualify:

1. **ripgrep subprocess in `repo.py`** — approved. Implementation adds *more*
   assertions than it removes: argv[0] is the `shutil.which` result, the user
   pattern only ever appears after a literal `--`, and neither `shell=True` nor
   `create_subprocess_shell` appears anywhere.
2. **`EXPECTED_BELT` changes** (Stage 3.5) — a deliberate amendment.
3. **The gate's out-of-range rewrite.** Today a citation whose file exists but
   whose line is out of range is deleted wholesale and hedged (`gate.py:60-71`).
   **DECIDED 2026-08-02: rewrite to the bare verified path AND keep the hedge.**
   The path survives (it is itself verified, so no grounding is given up), the
   unverifiable line number is stripped, and the honest "I couldn't confirm the
   exact line" sentence still lands. Strictly more informative than deletion and
   strictly no less honest. Breaks `tests/test_agent_gate.py:69-81` and `:84-91`,
   which are updated deliberately as part of this change.

## Known grounding risk this plan widens (and how it's bounded)

The gate verifies a line is *in range*, not that it's the *right* line
(`gate.py:50`). So a stale index can yield a right-file/stale-line citation the
gate passes. That risk exists today at the 2.0 s TTL; raising it and adding the
orientation brief widens it. Bounded three ways: cap the TTL at 10 s (not 60),
prune by directory mtime rather than trusting a long TTL blindly, and have the
orientation brief state in its own header — reinforced in `SYSTEM_PROMPT` —
that **it is a map, not evidence: read the file before citing a specific line.**

## Verification

- `uv run pytest` — the full 68-file suite. `tests/test_safety.py`,
  `tests/test_voice_streaming.py`, `tests/test_session.py`, and
  `tests/test_loop_guards.py` are the invariant guards; each intentional change
  to them is listed above.
- **New tests required**: parallel-tool history ordering under `gather`;
  barge-in cancelling a parallel tool round leaves history repairable;
  streaming-with-`allow_retry` on the text path; grep argv is flag-safe;
  gate line-count cache invalidates on mtime change; `symbol_context` on
  `tests/fixtures/sample_repo/`.
- **Latency harness** (Stage 0 delivers the traces; this consumes them):
  `pyrrhon/evals/grounding.py` gains `--json`, `--repeat N` for variance, and
  `--compare baseline.json` with a non-zero exit on regression. Point it at
  **Pyrrhon itself** (`--repo .`) — `tests/fixtures/sample_repo` is two files,
  so latency numbers from it are meaningless, whereas Pyrrhon is a real ~6k
  LOC repo with an interesting dependency graph and costs zero new fixtures.
- **Two eval gaps worth closing while there**: `evals/grounding.yaml` cases gain
  a `must_not_cite` form — **nothing today tests VISION.md criterion 3
  ("admits ignorance")** — and a new `evals/understanding.yaml` scores
  dependency/blast-radius questions ("what calls `greet`", "what breaks if I
  change `helpers.py`") against expected citation sets with a `min_recall`.
  That second file is the only way to prove Stage 3's quality claim.
- **End-to-end**: `uv run pyrrhon <some-large-non-Python-repo>` and ask
  "how does X work?" — before/after on the reported first-response time, in both
  `--text` and `--voice`. The stage-3 multi-language work is what makes that
  test meaningful on a repo that isn't Python.

---

# Addendum: local verification pass (2026-08-02)

The plan was checked line-by-line against the working tree at `0d57d3d`.
**Every code citation is accurate**, including the quantitative ones: the plan
estimates ~1543 tokens of tool schema, measured value is **1558** (6232 chars /
4, 15 tools). Two cosmetic corrections, already folded in above: `soul.py` is
`pyrrhon/core/agent/soul.py`, and the local ripgrep is 15.2.0 not 14.1.0.

## A1 — Compaction should be *cancelled*, not awaited (supersedes Stage 1.3)

The original text says `_run_turn_events` **awaits** any pending compaction
before starting the next turn. That reintroduces exactly the latency Stage 1.3
removes, just moved: if the user replies quickly, turn *N+1* blocks on turn
*N*'s summarization round trip.

Cancel it instead. This is safe and strictly better:

- `maybe_summarize` has exactly one await — `llm.chat` at `context.py:142` —
  and it happens **before** any mutation of `history`. The splice at
  `context.py:154` is synchronous.
- So a `CancelledError` at that await leaves history byte-identical, which is
  the contract `context.py:129-131` already documents: *"Any LLM failure leaves
  history untouched — compaction is an optimization, never a correctness
  requirement."*
- The `ContextLengthExceededError` recovery path (`loop.py:219-242`) remains
  the safety valve, so a repeatedly-cancelled compaction can never strand a
  turn — it just means the overflow path does the work synchronously when it
  actually matters.

Await only if the cancel races a compaction that already returned from
`llm.chat`; a `task.cancel()` followed by a suppressed `await task` handles
both cases in three lines.

## A2 — Pre-warm the LLM connection at startup (new, not in the plan)

`repl.py:52-70` already warms the symbol index in the background. Nothing warms
the *HTTP* path. The first `llm.chat` of a session pays DNS + TCP + TLS
handshake on top of model latency — several hundred ms, and it lands on turn
one, which is the turn that forms the user's impression of the product.

`AsyncOpenAI` holds an `httpx` pool, so a throwaway request at startup (or a
bare connection open) amortizes it to zero. Same background-task pattern as
`warm_index_in_background`, roughly 15 lines. This belongs in Stage 1.

## A3 — Tension between 4.1 and 2.2 (flagged, not blocking)

Stage 4.1 sends `tools=None` on social turns; Stage 2.2 works to keep the
prompt prefix byte-stable. Alternating between tools-attached and tools-absent
payloads produces two distinct prefixes. Most providers maintain a prefix
*tree* rather than a single cached prefix, so both stay warm and this is a
non-issue — but it is worth measuring with Stage 0's `schema_chars` mark rather
than assuming.

## A4 — Sequencing

Stages 0-2 are the latency work and are mechanically independent of Stage 3.
Stage 3.3 (multi-language) is the largest and least-specified item in the plan:
each grammar needs its own `_DEF_QUERY` / `_REF_QUERY` / import query with
different node names per language, plus the extension-set change noted above.
It should land last, and per-language, behind its own tests — not as one change.

---

# Implementation record (2026-08-02)

Branch `m10-latency-harness`, eight commits. **460 tests pass** (from 341, and
the suite was red on `beta` before any of this — see commit 1).

| Stage | Status | Commit |
|---|---|---|
| — baseline test fixes | done | `a91826d` |
| 0 telemetry | done | `bbec16b` |
| 1.1 stream everywhere | done | `450ee34` |
| 1.2 model knobs, 1.3 async compaction, A2 warm-up | done | `d00eccd` |
| 1.4 gate/generation overlap | **not done** — measured, see below | — |
| 2.1 parallel tools | done | `2a080e7` |
| 2.2 soul cap + schema memo | partly — see below | `0eaa4c6` |
| 2.3 ripgrep | done | `d97681f` |
| 2.4 caches | done | `236b900` |
| 3 code intelligence | **deferred by decision** | — |
| 4 conversational partner + gate policy | done | `2c4909b` |
| verification harness | done | `de93294` |

## What was measured

Same synthetic turn (three 80ms tools, real grounding gate), before and after:

```
before   turn 269ms   tool wall 264ms   total 264ms   speedup 1.00x
after    turn 101ms   tool wall  96ms   total 287ms   speedup 2.99x
```

Component measurements:

| | before | after |
|---|---|---|
| grounding gate, warm (`_check_sync`) | 0.72 ms | 0.025 ms (29x) |
| grounding gate end-to-end | 1.02 ms | 0.36 ms |
| soul prompt with a full `memory.md` | 26 427 chars / ~6 606 tok | 8 871 / ~2 217 (−66%) |
| grep, 13k-file tree, common pattern | 470 ms | 40 ms (11.9x) |
| grep, 13k-file tree, rare pattern | 4 964 ms | 857 ms (5.8x) |

## Four places the plan was wrong

Recorded because the reasoning matters more than the conclusions.

1. **"The prompt prefix is never stable" (defect #3) — false.** `system_content`
   derives from immutable inputs, so `history[0]` was already byte-identical
   across turns, and rebuilding the schema list already produced identical
   JSON. Re-assigning the same string changes nothing on the wire. The
   compare-before-assign was dropped as a no-op; the schema memo was kept, but
   documented as the CPU tidy it actually is. The *real* win in Stage 2.2 was
   the soul cap, which the plan itself ranked highest.

2. **Stage 1.4 (gate/generation overlap) is not worth doing.** A gate check
   costs ~1 ms against 50–500 ms of token generation between chunks — under 1%
   of the gap. Worse, deferring emission to the next stream event means a model
   that streams a chunk then stalls leaves it unspoken and absent from history,
   so a barge-in has nothing to truncate;
   `test_barge_in_mid_stream_leaves_partial_answer_in_history` caught it. Stage
   2.4's cache attacked the same millisecond and won 29x with no structural
   risk.

3. **ripgrep was initially *slower* than pure Python**, on every real query.
   The Python scan stops at `MAX_GREP_MATCHES` and abandons the walk; rg has no
   global `--max-count` and scanned the whole tree. Fixed by reading rg's stdout
   incrementally and killing it at the cap — 842 ms → 40 ms on the common case.
   Also: rg's text output format is genuinely ambiguous for filenames
   containing `-` or `:`, so the implementation uses `--json`.

4. **The gate policy change was narrower than predicted.** The plan expected
   `tests/test_agent_gate.py:69-91` to break; it didn't. Those cases cite files
   that genuinely do not exist, which still get the broad hedge. Only the
   out-of-range case changed.

## Decisions taken during implementation

- **Compaction is cancelled, not awaited** (addendum A1, agreed up front).
  Awaiting would hand turn N+1 the round trip removed from turn N.
- **`[model] max_tokens` defaults to None**, not a voice-appropriate cap. The
  plan's rationale was that unbounded generation drives time-to-first-output;
  Stage 1.1 severed that link, and a hard cap now only risks truncating
  mid-sentence. It is an opt-in runaway guard.
- **Gate out-of-range → bare path + narrowed hedge** (signed off).
- **Multi-language scope, when Stage 3 lands: TypeScript/JavaScript and Go.**

## Two bugs found in existing code

- **`time.monotonic()` has ~15.6 ms resolution on Windows**, so every telemetry
  span shorter than a tick measured exactly 0.0 — and `last_turn_latency_ms`,
  the voice budget metric, was quantising to the same tick. Both moved to
  `time.perf_counter()` (also monotonic, ~100 ns).
- **The suite was red before any M10 work**, for three unrelated reasons: a
  stale hex pinned in `test_branding`, `test_build_agent_m4` leaking the
  developer's real `~/.pyrrhon/config.toml`, and the TUI suites indexing the
  checked-in fixture repo — writing a gitignored `cache.db` that survived into
  later runs. `tests/conftest.py` now fences `tests/fixtures/` and fails the
  test that writes into it.

## What Stage 3 still owes

`symbol_context` (three round trips → one), PageRank repo map personalised by
conversation mentions, multi-language indexing (TS/JS + Go), the orientation
brief, and slimming the belt. `evals/understanding.yaml` is deferred with it —
it exists to prove Stage 3's quality claim and measures nothing without it.

Note `ast_index.py:_iter_files_with_mtime` hardcodes `.py`; the extension set
must become table-driven in the same change, or new grammars will never see a
file.
