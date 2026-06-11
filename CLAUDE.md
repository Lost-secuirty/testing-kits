# CLAUDE.md - testing-kits

> **Read this even if you are not Claude.** This file is auto-loaded by Claude Code, but
> the rules here are not Claude-specific. The canonical, tool-agnostic contract for every
> AI agent (and human) in this repo is **[`AGENTS.md`](AGENTS.md)** — read it first,
> whoever you are. Below are only Claude-Code-specific notes.

Auto-loaded by Claude Code. Read AGENTS.md first; it is the canonical contract for this repo. This file only adds Claude-specific notes.

## Claude-specific notes

- Follow AGENTS.md for commands, boundaries, git workflow, source-of-truth order, and the Working Agreement.
- Read SECURITY.md before writes, deletes, installs, permission changes, credential work, or outbound messages.
- For subagents, tell them to read AGENTS.md, SECURITY.md, and docs/LEARNINGS.md first, then report verified versus assumed facts.
- Do not edit .claude/, hooks, settings, or agent permissions unless explicitly asked.
- If push or a tool call is blocked, report the exact blocker and the next safe option. Do not claim persistence until the remote branch or commit is verified.
