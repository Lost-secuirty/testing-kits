# testing-kits

Testing harnesses for general use. Pure Python stdlib — zero external dependencies.

See [`HARNESS_INVENTORY.md`](./HARNESS_INVENTORY.md) for the full catalog of 43
harnesses (each with implementation + matching `test_*.py` suite, mock HTTP
server where applicable, and `--self-test` mode).

## Status

**43 harnesses | 3,079 tests | All green**

Harnesses 1–36 are general-purpose. Harnesses 37–43 are pharmacy-domain-specific
(added from `lostsoulfs/pharmacy-app` gap audit): SM-2 SRS algorithm, clinical
calculators, temporal PIN lockout, SQLite backup/restore lifecycle, rotating audit
log, date-window expiry alerting, and partial-fill two-phase ledger.

## Running

```bash
# All harnesses
python -m unittest discover -s . -p "test_*.py"

# Pharmacy batch only (fast, ~3s)
python -m unittest test_srs_test_harness test_clinical_calc_test_harness \
  test_lockout_test_harness test_backup_restore_test_harness \
  test_auditlog_cap_test_harness test_expiry_window_test_harness \
  test_partial_fill_test_harness

# Self-test mode (each harness, no server required)
python srs_test_harness.py --self-test
python clinical_calc_test_harness.py --self-test
python lockout_test_harness.py --self-test
python backup_restore_test_harness.py --self-test
python auditlog_cap_test_harness.py --self-test
python expiry_window_test_harness.py --self-test
python partial_fill_test_harness.py --self-test
```
