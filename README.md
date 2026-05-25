# Testing Harnesses

This workspace has two separate scopes:

1. **Generic harness suite** — the root-level `*_test_harness.py` files and
   their matching `test_*.py` suites.
2. **Dice Duel Reliability Lab** — isolated under `dice_duel_lab/`.

Dice Duel is a controlled app-under-test for reliability experiments. It is not
part of the generic harness count and should not be collected by root
`test_*.py` discovery.

## Generic Harness Commands

Static checks:

```bash
python3 tools/check_no_dice_bleed.py
python3 tools/check_harness_inventory.py
python3 tools/check_port_map.py
```

Run the generic harness tests:

```bash
python3 tools/run_harness_sweep.py
```

## Dice Duel Lab Commands

Run the Dice Duel unit suite:

```bash
cd dice_duel_lab
python3 -m unittest -q dice_duel_tests.py
```

Run the Dice Duel lab sweep:

```bash
cd dice_duel_lab
python3 run_dice_duel_lab_sweep.py
```

Check the Dice Duel guardrail:

```bash
cd dice_duel_lab
python3 dice_duel_guard.py --check
```

## Batch Rule

New generic harness work follows the project rule in `AGENTS.md`: max 6
harnesses per batch, not all at once.
