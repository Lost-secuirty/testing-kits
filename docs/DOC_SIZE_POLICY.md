# Doc size policy

Large documentation files are useful until they become unsafe to inspect or edit through connectors.

This repo should keep normal docs bounded and split large references before they become difficult for humans or AI systems to audit.

## Rule

Do not overwrite a large file from a truncated connector read.

If a connector response says the output was truncated, treat the file as unsafe for full replacement unless one of these is true:

- the file is fetched in complete bounded chunks and reconstructed exactly;
- a patch/diff tool can target only the intended range;
- the edit is made locally with full-file access and reviewed before commit.

## Size targets

Use these targets for human-authored docs:

| File type | Target | Split trigger |
|---|---:|---:|
| Quick-start / orientation docs | 50-150 lines | 200 lines |
| Concept docs | 100-250 lines | 350 lines |
| Operational guides | 150-300 lines | 400 lines |
| Inventories / generated maps | no strict target | split by category before connector truncation becomes common |
| Failure examples | 40-120 lines each | one failure class per file |

These are reviewability limits, not hard correctness rules.

## Split strategy

When a doc grows too large, split by purpose:

- orientation stays in `START_HERE.md`;
- proof theory stays in `ANTI_VACUITY_MODEL.md` and `PROOF_STRENGTH_LADDER.md`;
- porting stays in `PORTING_GUIDE.md`, `PROPERTY_BASED_PORTING.md`, and `INTEGRATION_LAYER_GUIDE.md`;
- AI usage stays in `AI_CONSUMPTION_GUIDE.md` and `AGENT_COMMUNICATION_GUIDE.md`;
- large inventories should split by category or be generated from structured data.

## Large inventory rule

`HARNESS_INVENTORY.md` is allowed to be large because it is a catalog.

However, large catalogs should not be the only entry point. Keep short reader-path docs and machine-readable indexes so reviewers do not need to load the whole catalog for basic status.

## Connector-safe editing checklist

Before editing a large doc through a connector:

- [ ] Did the fetch return the complete file?
- [ ] If not, can the file be fetched in complete line ranges?
- [ ] Is there a patch tool that can edit only the target range?
- [ ] Is the edit small enough to avoid full replacement?
- [ ] Is the original SHA known?
- [ ] Is the target branch correct?
- [ ] Is the file generated or derived?

If any answer is uncertain, stop and use a smaller companion doc instead of overwriting the large file.

## Practical implication

For this stabilization pass, prefer adding small focused docs over expanding one huge guide.

The repo should optimize for:

- fast human review;
- safe connector reads;
- low drift;
- clear source-of-truth boundaries;
- easy AI consumption without requiring full-context loading.
