# Pyrrhon implementation plans

One plan per milestone. Execute **in order** — each consumes the previous
milestone's interfaces:

| Order | Plan | Delivers |
|---|---|---|
| M0 | `2026-07-03-pyrrhon-m0-grounded-text-repl.md` | Grounded text REPL, `/init` + soul files |
| M1 | `2026-07-03-pyrrhon-m1-grounding-gate-memory.md` | Grounding gate, `remember`/memory.md, eval v0 |
| M2 | `2026-07-03-pyrrhon-m2-textual-tui.md` | Textual TUI, slash-command registry |
| M3 | `2026-07-03-pyrrhon-m3-pipecat-voice.md` | Voice, barge-in, Session, TruncateSpeech |
| M4 | `2026-07-03-pyrrhon-m4-deep-reasoning-tools.md` | AST index, git/web tools, think_deeper |
| M5 | `2026-07-03-pyrrhon-m5-mcp-extensibility.md` | MCP client, provider fallbacks, latency |
| M6 | `2026-07-03-pyrrhon-m6-design-mode.md` | Act 2: design mode, spec writing |
| M7 | `2026-07-03-pyrrhon-m7-plugin-loader.md` | Plugin loader, example plugin |

## How to execute (multi-agent)

- Default: one orchestrator session per milestone using
  `superpowers:subagent-driven-development` — fresh subagent per task,
  review between tasks.
- Parallel windows only for **disjoint file sets inside one milestone**
  (e.g. M4's AST / git / web task groups; M2's registry vs TUI panes), each
  in its own git worktree, merged back through one session.
- Every plan M1+ was written **before upstream code existed**. The
  Interfaces→Consumes blocks are the intended contracts; revalidate them
  against the actual codebase at execution time (each plan carries a drift
  warning saying the same).

## Known cross-plan drift (resolve at execution time)

Recorded during the 2026-07-04 consistency review; these are deliberate
leave-as-is items, not oversights:

1. **Sync vs async command handlers.** M2 builds the registry with sync
   handlers (`def handler(args: str, ctx: CommandContext) -> str`) and a
   sync `dispatch`. M3, M5, and M7 register `async def` handlers and M7
   awaits `dispatch`. Resolution: when M3 lands its first async handler,
   upgrade `dispatch` to `async def` that supports both (via
   `inspect.iscoroutinefunction`) and update M2's call sites — a small,
   deliberate refactor. Until then M2's sync shape stands.
2. **`CommandContext` field growth.** Canonical: `repo_root, agent, ui`
   (M2) + additive defaulted fields `mcp` (M5), `session` (M6),
   `plugins` (M7), and M3's `voice`. Every addition must keep a default so
   earlier construction sites never break.
3. **M3 attributes the command registry to "M1"** in its assumptions
   section — it is built in M2. Same interface, wrong label; harmless.
4. **Handler contract.** Handlers **return** the response string; they do
   not print via `ctx.ui`. `dispatch` returning `None` means "not a
   command" (the input goes to the LLM), so a handler must never return
   `None`.
