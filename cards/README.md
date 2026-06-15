# Test-Harness Cards + Teeth Ratchet (staged)

**Status: staged / additive.** Everything for this feature lives in this `cards/` directory
plus `tests/tools/test_harness_card.py`. It touches **no existing file** — no harness, no
`Makefile`, no CI workflow — so merging it changes nothing operational until you wire in the
one gate step below. It is pure-stdlib (fits the testing-kits charter) and depends only on the
existing `tools/proof_audit.py` + `tools/harness_registry.py`, so it must land **after** the
Batch 0 teeth gate (the branch this was built on).

## Why this exists — the gate's blind spot
`tools/proof_audit.py` derives a harness's `required` scope purely from a live, module-level
`TEETH` declaration. So if a harness that earned `required` later **loses its `TEETH`** (a bad
refactor, a botched merge), the gate silently re-classifies it as `pending` — which is
**non-blocking**. The teeth regression ships green. Nothing pins what a harness already earned.

The ratchet fixes that: `cards/teeth_ratchet.json` records the status each harness has earned;
`--check` fails loud if any harness's live status ranks **below** its pin. Status may only
ratchet up (`legacy → pending → required`), never silently fall back.

It also produces the committed per-harness **card** the repo lacks today (the gate's `STATUS.json`
is regenerated + gitignored): each card shows what a harness **is** (docstring), what it
**catches** (teeth kind / mutants / corpus), and how it is **tested** (paired test / proof /
self-test).

## Files
| File | What |
|---|---|
| `cards/harness_card.py` | the tool — build cards, write artifacts, ratchet `--check`, `--self-test` |
| `cards/cards.json` | committed per-harness card data (machine-readable) |
| `cards/CARDS.md` | committed per-harness cards (human-readable) |
| `cards/teeth_ratchet.json` | the committed status pin the `--check` gate enforces (authoritative) |
| `tests/tools/test_harness_card.py` | fast unittest of the ratchet logic + the tool's self-test |

## Run
```bash
python cards/harness_card.py --self-test                 # prove the ratchet has teeth
python cards/harness_card.py --check                     # gate: fail on a teeth regression
python cards/harness_card.py --write --update-ratchet    # regenerate cards + raise pins
```
Regenerate (`--write`) and review the diff whenever harness statuses change. `--update-ratchet`
only ever *raises* a pin; lowering a pin (intentionally retiring a harness's `required` status)
is a deliberate, reviewable edit to `teeth_ratchet.json`.

## Wiring it in (do this AFTER Batch 0 merges to main)
1. Add a Makefile target:
   ```makefile
   cards:
   	$(PY) cards/harness_card.py --check
   ```
2. Add one step to `.github/workflows/test.yml` after `make proof`:
   ```yaml
   - name: Teeth ratchet (no required harness may regress)
     run: make cards
   ```
3. Optionally regenerate `CARDS.md`/`cards.json` in `make report` so the committed cards stay fresh.

> Perf note: `--check` re-runs the live teeth swap-check (a subprocess per non-legacy
> harness), so running it right after `make proof` would duplicate that stage. A
> perf-conscious wire-in should make it its own stage or have it consume the gate's
> already-computed output (`STATUS.json`) instead of re-invoking the swap-check.

Until then, `make test` already exercises `tests/tools/test_harness_card.py`, so the ratchet
logic is covered; only the live `--check` gate is left un-wired by design.
