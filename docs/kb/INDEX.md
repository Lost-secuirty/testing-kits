# docs/kb — the machine-to-machine knowledge base

Curated, **version-pinned** crib sheets for any tool this repo uses that sits outside (or past
the cutoff of) an agent's training data, plus per-agent journals. Plain markdown, small files,
one tool per sheet — built for fast agent-to-agent transfer (copy this folder to another repo
and the contract works there too).

> **Stack note (testing-kits):** this is a **pure-stdlib Python** repo with zero runtime
> dependencies (`pyproject.toml`), so there is little fast-moving library surface to document.
> Expect **few or no crib sheets** — the durable value here is the per-agent **journal** and
> the contract itself. Add a sheet only when you hit a real, non-obvious tool gotcha (e.g. a
> `unittest` / `ruff` / CI quirk worth not re-learning). Do **not** import sheets from other
> repos' stacks (no Node/Vite/Pixi here — wrong stack).

Evidence basis: docs-in-context lifts coding-agent performance most for less-common /
fast-moving libraries, and *working code examples* help more than prose (arXiv 2503.15231).

## The contract (binds every agent and subagent)

1. **READ before working with a listed tool:** open this INDEX, then only the sheet(s) you
   need (progressive disclosure — don't bulk-load the folder).
2. **WRITE what you verify:** a new gotcha/fix/tool-fact → append to the matching sheet under
   the right section, tagged `[agent · date · VERIFIED|SECONDARY|MYTH]`. Append-only; mark
   superseded entries `SUPERSEDED:` with a reason — never delete another agent's entry.
3. **Your session story goes in `journal/<agent>.md`**, not in the sheets. Sheets hold durable
   tool facts; journals hold what *you* did, tried, and suspect. Read your OWN journal on
   session start; never edit another agent's journal.
4. **Scope:** kb = the stack (tool facts). Project gotchas stay in `docs/LEARNINGS.md`; the
   harness/proof standards stay in `docs/PROOF_TEST_STANDARD.md` and the AI_* docs. When a
   LEARNINGS entry is really a *tool* fact, distill it here and link back.

## Catalog

| Sheet | Covers | Pinned at |
| --- | --- | --- |
| _(none yet — pure-stdlib stack; add one only on a real tool gotcha, copy `TEMPLATE.md`)_ | | |

## Journals

| Agent | File |
| --- | --- |
| Claude (Claude Code sessions) | [journal/claude.md](journal/claude.md) |

New sheet = copy [TEMPLATE.md](TEMPLATE.md), add a Catalog row here, done.
