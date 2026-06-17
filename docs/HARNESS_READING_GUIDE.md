# Harness Reading Guide

Purpose: make the harness library readable by humans first and easy for AI agents to navigate later, without turning `AGENTS.md` into a bloated instruction file.

This document is descriptive. It is **not** an instruction override. For operating rules, use `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

## Reader layers

Use the smallest layer that answers the question.

| Layer | Best for | Files |
| --- | --- | --- |
| 0. Orientation | Fast understanding of what the repo is and is not | `README.md`, `docs/WALKTHROUGH.md` |
| 1. Rules | Safe operation before edits | `AGENTS.md`, `CLAUDE.md`, `SECURITY.md` |
| 2. Inventory | Finding an existing harness and avoiding duplicates | `HARNESS_INVENTORY.md`, `HARNESS_ROADMAP.md` |
| 3. Proof model | Understanding whether a harness actually bites | `README.md` proof model, `tools/proof_audit.py`, `tools/vacuity_gate.py`, `docs/LEARNINGS.md` |
| 4. Implementation | Reading or modifying one harness | `harnesses/<category>/<name>_test_harness.py`, paired `tests/<category>/test_<name>_test_harness.py` |
| 5. Gate machinery | Maintaining the proof system itself | `Makefile`, `tools/proof_audit.py`, `tools/vacuity_gate.py`, `tools/gate_canary.py`, `tools/file_guard.py`, `.github/control-policy.json` |

## Human reading path

For a reviewer:

1. Read `README.md` to understand the repo boundary.
2. Read `HARNESS_INVENTORY.md` to choose a harness.
3. Open the harness file and its paired test.
4. Run the smallest relevant command first:
   - one harness: `python harnesses/<category>/<name>_test_harness.py --self-test`
   - category: `make test-core`, `make test-security`, `make test-ai`, or `make test-pharmacy`
   - proof surface: `make teeth`, then `make proof`
5. Use `docs/LEARNINGS.md` only for gotchas and historical context. Do not treat it as canonical if live code disagrees.

For the maintainer:

1. Check whether the change is harness logic, documentation, or gate machinery.
2. For harness logic, require paired tests and TEETH evidence where applicable.
3. For documentation, keep claims tied to command names, file paths, or verified output.
4. For gate machinery, run `make canary`, `make guard`, and the specific gate being changed.

## AI reading path

For AI agents, the expected retrieval order is:

1. `AGENTS.md`, `CLAUDE.md`, `SECURITY.md` for operating boundaries.
2. `llms.txt` for the compact navigation map.
3. `README.md` for public repo shape.
4. `HARNESS_INVENTORY.md` and `HARNESS_ROADMAP.md` to avoid duplicate harness work.
5. The specific harness + paired test for the actual task.
6. `docs/LEARNINGS.md` only after locating the relevant area.

Do not load every document by default. Broad loading increases stale-context risk and makes agents more likely to follow historical notes over live code.

## Harness dossier shape

When documenting or auditing a harness, capture these fields. Keep them factual and compact.

```text
Name:
Path:
Category:
Failure class:
Oracle:
Planted mutant(s):
Corpus / fixtures:
Proof status: required | pending | legacy
Vacuity target(s): none | symbol list
Commands:
Known limits:
Nearest related harnesses:
```

Use this structure in future inventory expansions, PR summaries, or per-harness docs. Do not duplicate full source code into docs.

## Expansion policy

Good expansion:

- Adds orientation that prevents duplicate work.
- Explains why a harness exists, what bug it catches, and where its proof lives.
- Links to source files instead of restating implementation.
- Separates human narrative from AI navigation.
- Marks unverified counts as loaded state, not fresh proof.

Bad expansion:

- Adds new behavioral rules outside `AGENTS.md`.
- Turns README into a full manual.
- Repeats long code snippets from harnesses.
- Treats historical `docs/LEARNINGS.md` entries as current truth without checking live files.
- Claims all harnesses are fully proven when some are still `pending` or `legacy`.

## Current next layer

The next useful layer is a per-harness dossier index derived from `HARNESS_INVENTORY.md` plus live TEETH/vacuity status. Build it as a generated or mechanically checkable artifact if possible; avoid hand-maintaining counts that can drift.
