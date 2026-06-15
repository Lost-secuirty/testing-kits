# Proof Test Standard

Every new harness must prove two things:

1. The safe fixture passes.
2. The planted bad fixture fails.

The minimum shape is:

- `harnesses/<category>/<name>_test_harness.py` — self-contained harness with `--self-test`.
- `tests/<category>/test_<name>_test_harness.py` — API and CLI tests.
- `tests/<category>/test_<name>_proof.py` — planted-bug proof when the paired test alone cannot prove failure detection.

Existing harnesses are audited by `make proof`. Separate proof files are required only when safe/bad control evidence is not already explicit in the self-test or paired unittest.

Coverage can show code was exercised, but it does not prove the test would catch a real bug. Use proof fixtures or mutation probes for that.

This standard is a current proof baseline, not total correctness proof. It shows
that fixture-defined safe cases pass and planted bad cases fail under current
tooling.

## Hardened gate — the TEETH contract (2026 upgrade campaign)

The audit (`tools/proof_audit.py`) was hardened so "proven" requires *real evidence
a harness catches a bug*, not the mere presence of keyword markers in the source.
The mechanism is the shared, pure-stdlib `harnesses/_teeth.py` contract.

Each in-scope harness declares a module-level `TEETH = Teeth(...)` that points at
its own correct `oracle`, one-or-more intentionally `Mutant` (buggy) twins, and a
`prove(impl) -> bool` predicate that returns `True` iff `impl` is *caught* against
the harness's frozen fixture corpus. The gate then asserts:

- `prove(oracle) is False` — the correct implementation is not flagged;
- `prove(mutant.impl) is True` for every mutant — every planted bug is caught;
- `corpus_size >= 1` — the proof ran against real fixtures.

`prove` must be pure and deterministic (no clock/network/filesystem I/O; seed any
RNG). The check runs subprocess-isolated (`tools/teeth_check.py`) so a broken
harness fails only itself. The cross-platform stdlib swap-check is the **mandatory**
gate; `tools/mutmut_lane.py` (mutmut, Linux/WSL only) is an **advisory** deepening
that catches "vacuous green" the swap-check cannot.

### Scopes

- **required** — a non-legacy harness that declares `TEETH`. It MUST pass the
  swap-check, have a paired unittest, and a green self-test. Declaring `TEETH` is
  the opt-in; there is no separate allowlist.
- **pending** — a non-legacy harness with no `TEETH` yet. Reported and counted but
  NOT blocking, so the gate is honest-strong without red-locking `main` mid-campaign.
  Each batch moves harnesses from `pending` to `required`.
- **legacy** — out-of-campaign categories (`pharmacy`) keep the older keyword/
  self-test soft check.

New and upgraded harnesses follow `template/harness_template.py`, which ships the
`TEETH` + `Report` (`--self-test` / `--json`) shape. Verify locally with
`make teeth` (or `python tools/proof_audit.py`).
