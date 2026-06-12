# CLAUDE.md - testing-kits

The filename is historical. This is a universal instruction source for every human,
agent, and automation system working in this repository. Read it together with
`AGENTS.md` and `SECURITY.md`; all rules below apply regardless of the tool in use.

## Operational notes

- Follow AGENTS.md for commands, boundaries, git workflow, source-of-truth order, and the Working Agreement.
- Read SECURITY.md before writes, deletes, installs, permission changes, credential work, or outbound messages.
- For subagents, tell them to read AGENTS.md, SECURITY.md, and docs/LEARNINGS.md first, then report verified versus assumed facts.
- Do not edit .claude/, hooks, settings, or agent permissions unless explicitly asked.
- If push or a tool call is blocked, report the exact blocker and the next safe option. Do not claim persistence until the remote branch or commit is verified.
