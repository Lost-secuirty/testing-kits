# Test observability

A test system should be observable.

If a repo cannot explain what changed, what is required, what is pending, what is legacy, and what proof was run, the test system becomes technical debt.

## Tracked signals

Track these signals when reporting repo health:

- total harness count;
- required harness count;
- pending harness count;
- legacy harness count;
- failing harness count;
- self-test pass count;
- proof pass count;
- CI duration;
- known slow lanes;
- flake incidents;
- false-positive incidents;
- false-negative incidents;
- mutation/advisory results;
- docs sync state.

## Current snapshot table

This table is a human-readable index. Proof output wins if this table becomes stale.

| Date | Source | Total | Required | Pending | Legacy | Failing | Proof result | Notes |
|---|---|---:|---:|---:|---:|---:|---|---|
| 2026-06-18 | `docs/UPGRADE_CAMPAIGN.md` Batch 9 | 77 | 69 | 0 | 8 | 0 | documented green | final pending TEETH campaign completed for non-pharmacy harnesses |

## Docs sync checks

When updating docs, check for drift across:

- `README.md`;
- `docs/GOLDEN_STATS.md`;
- `docs/UPGRADE_CAMPAIGN.md`;
- `HARNESS_INVENTORY.md`;
- `cards/teeth_ratchet.json`, if present;
- generated `STATUS.md` / `STATUS.json` artifacts, if produced.

## Incident vocabulary

### False positive

The harness reports a failure when the correct oracle or safe fixture should pass.

### False negative

The harness misses a planted-bad case or mutant.

### Flake

The result changes without a relevant code or fixture change.

### Docs drift

Human-readable docs disagree with generated status, proof output, or campaign records.

### Proof drift

A harness is documented as required or TEETH-proven but the current proof gate no longer supports that claim.

## Reporting format

Use this shape for future status notes:

```md
## Test observability report

- Date:
- Branch:
- Commit:
- Total harnesses:
- Required:
- Pending:
- Legacy:
- Failing:
- Commands run:
- Commands not run:
- CI status:
- Docs drift found:
- Proof drift found:
- Next safe action:
```

## Rule

Do not report a fresh green state from memory.

Fresh status requires command output, CI output, or named generated artifacts.
