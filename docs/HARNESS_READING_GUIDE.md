# Harness Reading Guide

Purpose: make the harness library readable by humans first and easy for AI agents to navigate later, without turning `AGENTS.md` into a bloated instruction file.

This document is descriptive. It is **not** an instruction override. For operating rules, use `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

## Reader layers

Use the smallest layer that answers the question.

| Layer | Best for | Files |
| --- | --- | --- |
| 0. Orientation | Fast understanding of what the repo is and is not | `README.md`, `docs/WALKTHROUGH.md`, `docs/DOCS_MAP.md` |
| 1. Rules | Safe operation before edits | `AGENTS.md`, `CLAUDE.md`, `SECURITY.md` |
| 2. Inventory | Finding an existing harness and avoiding duplicates | `HARNESS_INVENTORY.md`, `HARNESS_ROADMAP.md` |
| 3. Proof model | Understanding whether a harness actually bites | `README.md` proof baseline, `docs/PROOF_TEST_STANDARD.md`, `tools/proof_audit.py`, `docs/LEARNINGS.md` |
| 4. Harness map | Understanding what a harness tests, what wider pattern it belongs to, and what it does not prove | `docs/HARNESS_MAP.md`, `HARNESS_INVENTORY.md`, source + paired test |
| 5. Implementation | Reading or modifying one harness | `harnesses/<category>/<name>_test_harness.py`, paired `tests/<category>/test_<name>_test_harness.py` |
| 6. Gate machinery | Maintaining the proof system itself | `Makefile`, `tools/proof_audit.py`, `.github/control-policy.json`, gate tools documented in the repo |

## Human reading path

For a reviewer:

1. Read `README.md` to understand the public boundary.
2. Read `docs/REVIEWER_QUICKSTART.md` for proof-baseline language and a sample trace.
3. Read `HARNESS_INVENTORY.md` to choose a harness.
4. Read `docs/HARNESS_MAP.md` when you need the harness's failure class, logic shape, outside testing pattern, and known limits.
5. Open the harness file and its paired test.
6. Run the smallest relevant command first:
   - one harness: `python harnesses/<category>/<name>_test_harness.py --self-test`
   - category: `make test-core`, `make test-security`, `make test-ai`, or `make test-pharmacy`
   - proof surface: `make proof`
7. Use `docs/LEARNINGS.md` only for gotchas and historical context. Do not treat it as canonical if live code disagrees.

For the maintainer:

1. Check whether the change is harness logic, documentation, or gate machinery.
2. For harness logic, require paired tests and TEETH evidence where applicable.
3. For documentation, keep claims tied to command names, file paths, source fixtures, or verified output.
4. For mapping docs, state current proof status as subject to change; do not turn a mapping entry into a permanent status pin.
5. For gate machinery, run the specific gate being changed and do not claim CI green until CI reports green.

## AI reading path

For AI agents, the expected retrieval order is:

1. `AGENTS.md`, `CLAUDE.md`, `SECURITY.md` for operating boundaries.
2. `llms.txt` for the compact navigation map.
3. `README.md` for public repo shape.
4. `docs/DOCS_MAP.md` and this guide for reading paths.
5. `HARNESS_INVENTORY.md`, `HARNESS_ROADMAP.md`, and `docs/HARNESS_MAP.md` to avoid duplicate or stale harness work.
6. The specific harness + paired test for the actual task.
7. `docs/LEARNINGS.md` only after locating the relevant area.

Do not load every document by default. Broad loading increases stale-context risk and makes agents more likely to follow historical notes over live code.

## Harness dossier shape

When documenting or auditing a harness, capture these fields. Keep them factual and compact.

```text
Name:
Path:
Category:
Failure class:
Logic shape:
Good case:
Planted-bad case:
Oracle / proof target:
External testing pattern:
Current outside reference:
Proof status:
Commands:
Known limits:
Related harnesses:
Docs touched:
```

Use this structure in future inventory expansions, PR summaries, or per-harness docs. Do not duplicate full source code into docs. Do not claim a `pending` harness has TEETH proof; document it as current-state evidence subject to future upgrade.

## Logic-shape labels

Use logic labels as proof-shape shorthand, not as formal mathematical proof.

- `AND` — all named checks must pass.
- `NOT` — a forbidden condition must not appear.
- `NAND` — two dangerous conditions must never both be true.
- `XOR` — exactly one path, route, or state should be valid.
- `XNOR` — implementation behavior and oracle/frozen expectation must agree.

## Batch closeout rule

Every harness-mapping batch must end with a documentation closeout pass.

At minimum, check whether the batch changed any of these:

- `HARNESS_INVENTORY.md`
- `HARNESS_ROADMAP.md`
- `docs/HARNESS_READING_GUIDE.md`
- `docs/DOCS_MAP.md`
- `docs/REVIEWER_QUICKSTART.md`
- `README.md`
- any new per-category or per-harness mapping docs

If proof counts, TEETH status, categories, harness names, or public claims changed, update the relevant docs in the same PR before marking it ready. Do not leave count or proof-status wording stale.

## Expansion policy

Good expansion:

- Adds orientation that prevents duplicate work.
- Explains why a harness exists, what bug it catches, and where its proof lives.
- Links to source files instead of restating implementation.
- Separates human narrative from AI navigation.
- Marks unverified counts as loaded state, not fresh proof.
- States that map entries may change as harnesses are ratcheted, renamed, split, or strengthened.

Bad expansion:

- Adds new behavioral rules outside `AGENTS.md`.
- Turns README into a full manual.
- Repeats long code snippets from harnesses.
- Treats historical `docs/LEARNINGS.md` entries as current truth without checking live files.
- Claims all harnesses are fully proven when some are still `pending` or `legacy`.

## Current next layer

Continue `docs/HARNESS_MAP.md` in small batches derived from `HARNESS_INVENTORY.md` plus live TEETH status. Prefer mechanically checkable status sources where possible; avoid hand-maintaining proof counts that can drift.
