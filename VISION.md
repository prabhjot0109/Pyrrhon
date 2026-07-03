# Pyrrhon — Vision (First Draft)

> Status: first draft. Committed to two acts (Understand → Design). Everything
> outside those two acts is parked in "Out of scope (for now)" on purpose.

## What it is

A voice-first engineering companion that lives in your terminal. You talk to it;
it talks back. It does two things:

1. **Understand** a codebase you didn't write — by discussing it out loud.
2. **Design** a codebase you're about to write — by arguing about it out loud,
   then writing the spec.

It is **voice-first, screen-supported**: voice drives the conversation, the
terminal shows the code, the file paths, and the diagrams it's referring to.
It is not voice-only. You need to see the `file:line` while it talks about it.

## The problem (specific, and real)

To understand a large or open-source project today, the loop is: clone, open the
editor, read the README, jump between files, search symbols, paste snippets into
a chat, ask, repeat — for hours. It's lonely and exhausting.

NotebookLM proved people learn well by *listening and interrupting*. But it can't
take a GitHub repo, so there's no way to talk to your own codebase. Coding agents
(Cursor, Copilot, Claude Code) are optimized for *editing* code, not *teaching you*
the code. Nothing today lets you put on headphones, point at a repo, and say
"walk me through how auth works" — then interrupt with "wait, why middleware?".

That gap is the whole reason this exists.

## Who it's for (first user = you)

The first user is a developer who wants to **contribute to an open-source project**
or **onboard to an unfamiliar codebase** and needs to understand its architecture,
data flow, and design decisions fast — by talking, not by reading for six hours.

If it's undeniably good for that one person, the other audiences (students,
interview prep, enterprise onboarding) come later. They are not the first user.

## The core principle: grounded, cited, spoken reasoning

This is what separates Pyrrhon from "a chatbot vaguely narrating about code."

Every answer must expose its reasoning path and cite real locations:

> "Auth starts at `middleware/auth.py:12`. Every request passes through it, the
> JWT is validated here, then reaches the handler. They chose middleware over
> per-route decorators — that decision is in commit `a3f19c`. Want the code?"

If it can't ground a claim in a real `file:line` or commit, it says so instead of
inventing one. Confident hallucination spoken out loud is the worst failure mode
for a voice product, so grounding is a hard requirement, not a nice-to-have.

## Act 1 — Understand (the wedge)

Point Pyrrhon at a repo. Then, in conversation:

- "Explain how authentication works." → grounded walkthrough, cites files.
- "Wait, why middleware instead of decorators?" → interruptible, answers immediately.
- "Show me the exact code." → the terminal opens the file at that line.
- "Where would I add feature X?" → traces the relevant path.
- "What changed in the last month?" / "Who owns this module?" → git-aware.

Interruption is a first-class feature, not turn-taking. You can cut in mid-sentence.

## Act 2 — Design (the second act)

You describe something you want to build. Pyrrhon does **not** immediately agree
or start generating. It interrogates, like a senior architect:

> You: "Let's use MongoDB."
> Pyrrhon: "Your data looks relational — users, orders, joins. What specific
> benefit are you expecting from Mongo over Postgres here?"

It challenges assumptions (skeptic spirit — suspend judgment until justified),
and only after the reasoning is clear does it write the artifacts:

- `PRD.md`, `HLD.md`, `LLD.md`, `api.md`, `database.md`, `risks.md`

**The conversation is the product. The Markdown is the artifact.** Framing it as
"AI that writes specs" undersells it; it reasons until the spec is obvious, then
writes it down.

## Why not just use Claude Code / Cursor / NotebookLM?

Honest answer: in **text**, agents can already read repos, explain them, and write
specs. Pyrrhon is not a new capability — it's a different interface plus one hard
part.

- **NotebookLM**: great conversational learning, can't take a repo.
- **Cursor / Copilot / Claude Code**: great at editing code in text, no
  full-duplex interruptible voice, not built to *teach* the codebase.
- **Pyrrhon**: voice-first, interruptible, grounded in `file:line`, built for
  understanding and designing rather than editing.

The moat is quality of experience (latency, interruption, grounding), not access
to a secret capability. A weekend toy is easy; a version you'd actually use every
week is the entire project.

## Out of scope (for now — parked on purpose)

Listed so they don't creep into v1. Each is a possible later act, not day-one work:

- Enterprise knowledge-transfer / new-joiner onboarding as a product (a sales
  problem, not a build problem, until the core loop is great).
- Students / teachers / interview-prep positioning (same core, different marketing —
  earn it later).
- Plugin *loader/marketplace* (amended 2026-07-03: v1 ships the extension
  *seams* it needs anyway — provider registry, MCP client, slash commands; a
  plugin loader over those seams is a late milestone, after the Understand
  loop works. See `docs/superpowers/specs/2026-07-03-pyrrhon-v1-design.md`).
- Custom company engineering standards / naming conventions enforcement.
- Architecture knowledge graph, multi-agent orchestration, whiteboard/diagram
  generation — pursue only if the plain version proves the loop first.


## Success criteria (what "it works" means — verifiable)

v1 is done when, on a real open-source repo neither of us wrote:

1. I can ask "how does <feature> work?" out loud and get a spoken answer that
   cites at least one correct `file:line`, confirmed by opening it.
2. I can interrupt mid-answer and it stops and responds to the interruption.
3. When it doesn't know, it says so instead of inventing a file path.
4. In design mode, it pushes back on at least one questionable choice before
   writing a spec, and produces a `PRD.md` I'd actually keep.

If those four hold on one repo, the wedge is proven.

## Open questions

- Repo grounding: MCP on-demand reads vs a pre-built index — which gives good
  enough citations without over-building? (Start with the former.)
- Interruption UX in a terminal: how does barge-in feel with `textual` rendering
  code at the same time?
- Grounding accuracy: how do we measure "cited the right `file:line`" so we can
  tell whether the agent is trustworthy or just confident?
