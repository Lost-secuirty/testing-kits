# Harness Map Index

This directory is the modular landing area for harness-map batches. It exists to keep mapping work reviewable and to avoid full-file replacement of large Markdown documents through truncated connector output.

This index is descriptive only. Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

## Why this exists

The central `docs/HARNESS_MAP.md` grew large enough that connector reads can return truncated content. Since GitHub file replacement requires the complete file body, replacing that central document from a truncated view risks deleting earlier mapping entries.

Going forward, new mapping batches should live in small batch files that can be fully read, reviewed, and merged safely.

## Current mapped batches

- Batch 1: core foundation harnesses. Current location: `docs/HARNESS_MAP.md`.
- Batch 2: security and resilience foundation harnesses. Current location: `docs/HARNESS_MAP.md`.
- Batch 3: property, mutation, snapshot, contract, and serialization harnesses. Current location: `docs/HARNESS_MAP.md`.
- Batch 4: config, logging, network, pipeline, and datetime harnesses. Current location: `docs/HARNESS_MAP_BATCH_4.md`.

- Batch 5: inventory #21-#25. Current location: `docs/harness-map/batch-05-idempotency-statemachine-numeric-authz-llm-eval.md`.

## Preferred future layout

Use small files such as:

- `docs/harness-map/batch-05-*.md`
- `docs/harness-map/batch-06-*.md`
- `docs/harness-map/batch-07-*.md`

A later dedicated consolidation PR may move earlier batch material into this directory, but active mapping PRs should not combine mapping work with file moves or consolidation.

## Batch-file rules

Each batch file should include:

- batch number and inventory range;
- harness name and path;
- category;
- failure class;
- logic shape;
- good case;
- planted-bad case;
- oracle / proof target;
- external testing pattern;
- current outside reference;
- proof status;
- commands;
- known limits;
- related harnesses;
- batch closeout listing the docs and ratchet files checked.

## Scope rules for mapping PRs

Mapping PRs should remain docs-only. Do not change:

- harness code;
- tests;
- workflows;
- hooks;
- dependencies;
- dashboard code;
- generated status files.

If any non-doc file changes during a mapping pass, stop and review the scope before continuing.

## Merge gate

Before merge, verify:

- changed files are docs-only;
- pending vs required language matches `cards/teeth_ratchet.json`;
- CI is green;
- required review bots have finished;
- required automated review comments are resolved;
- the PR body states proof limits and does not claim proof upgrades.
