# Pyrrhon

**A senior-engineer voice agent that helps you understand, reason about, and
design software through natural conversation.**

Named after [Pyrrho of Elis](https://en.wikipedia.org/wiki/Pyrrho), founder of
philosophical skepticism: suspend judgment and question every assumption before
accepting it. Pyrrhon doesn't just explain code — it refuses to accept a design
choice until you've justified it.

## The gap it fills

NotebookLM proved people learn well by *listening and interrupting* — but it
can't take a GitHub repo, so there's no way to talk to a codebase. Coding agents
(Cursor, Copilot, Claude Code) are built to *edit* code, not to *teach* it to you.

Nothing today lets you put on headphones, point at a repo, and say
*"walk me through how auth works"* — then cut in with *"wait, why middleware?"*.

Pyrrhon is a standalone product that solves that one problem.

## What it does

**Voice-first, screen-supported.** Voice drives the conversation; the terminal
shows the code, file paths, and diagrams it refers to. Podcast-style, and
interruptible — you can cut in mid-sentence.

It does two things:

1. **Understand** a codebase you didn't write. Point it at a repo and ask how a
   feature works, where to add something, what changed recently, who owns a
   module. Answers are grounded: every claim cites a real `file:line` or commit,
   or it says it doesn't know rather than inventing one.

2. **Design** a system you're about to build. Describe the idea; Pyrrhon
   interrogates it like a senior architect — *"your data looks relational, what
   do you expect Mongo to buy you over Postgres?"* — and only once the reasoning
   is clear does it write the spec (`PRD.md`, `HLD.md`, `LLD.md`, ...). The
   conversation is the product; the Markdown is the artifact.

Multi-tool: web search and MCP-based tools/skills extend what it can reason over.

## Why it's different

The capability to read and explain a repo already exists in text. Pyrrhon's edge
is the interface and one hard part: a low-latency, interruptible, full-duplex
voice loop, with answers grounded in real source locations so it never
hallucinates confidently out loud. A weekend toy is easy; a version you use every
week is the whole project.

## Status

Early. Building the Act 1 (Understand) loop first. See [VISION.md](VISION.md) for
scope, the two acts in detail, and verifiable v1 success criteria.


## Not in scope (yet)

Enterprise onboarding-as-a-product, student/interview-prep positioning, plugin
marketplace, company-standards enforcement. Each is a possible later act, parked
until the core Understand loop is undeniable.
