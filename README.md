# testing-kits

Testing harnesses for general use. Pure Python stdlib — zero external dependencies.

See [`HARNESS_INVENTORY.md`](./HARNESS_INVENTORY.md) for the full catalog of 36
harnesses (each with implementation + matching `test_*.py` suite, mock HTTP
server, and `--self-test` mode).

## Status

Being copied in one harness at a time. Currently in the repo:

| # | Harness | File | Tests |
|---|---------|------|-------|
| 1 | Stress  | `stress_harness.py` | `test_stress_harness.py` (52 tests) |

## Running

```bash
python -m unittest discover -s . -p "test_*.py"
```
