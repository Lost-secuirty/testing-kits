# Combinatorial coverage batch 1 learnings

Status: temporary batch-specific learning note.
Date: 2026-06-27.

## Avoiding exhaustive-test overclaims

A pairwise or t-way harness is not an exhaustive tester. It checks selected interactions for a declared finite model.

Repo-safe wording:

```text
This harness proves pairwise coverage accounting for a declared finite model and catches planted coverage mutants.
```

Unsafe wording:

```text
This harness tests all possible combinations for real software.
```

## Generated-input boundary

Generated or reduced suites do not replace TEETH. A generated-input layer becomes durable only when important cases are frozen into deterministic proof evidence.

## Connector editing boundary

Large catalog files such as `HARNESS_INVENTORY.md` and `docs/HARNESS_MAP.md` should not be full-replaced from truncated connector reads. Use a full checkout or a dedicated narrow closeout PR when reconciling those files.
