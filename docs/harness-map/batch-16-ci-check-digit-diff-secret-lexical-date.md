# Harness Map Batch 16

This file maps inventory entries #74-#77 in order: `security/ci_workflow_hardening`, `core/check_digit_identifier`, `security/diff_secret_gate`, `core/lexical_date_canonicalization`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `security/ci_workflow_hardening` = `required`, `core/check_digit_identifier` = `required`, `security/diff_secret_gate` = `required`, `core/lexical_date_canonicalization` = `required`.

## 74. CI Workflow Hardening Test Harness

- Name: CI Workflow Hardening Test Harness
- Path: `harnesses/security/ci_workflow_hardening_test_harness.py`
- Category: `security`
- Failure class: Audits GitHub Actions workflow definitions for the poisoned-pipeline / pwn-request class — a CI-config attack surface neither Supply-Chain (#34, dependency hashes/slopsquat) nor App-Security (#36) nor CWE/KEV (#68) covers. `audit_workflow` flags: action `uses` refs not pinned to a full 40-hex commit SHA, `pull_request_target` (including the YAML `on`→boolean-`True` key quirk), fork checkout via `with.ref: github.head_ref`, missing top-level `permissions`, missing per-job `timeout-minutes`, `actions/checkout` without `persist-credentials: false`, fork-PR scan skips (`head.repo.full_name == github.repository`), and ungated `concurrency`. Stdlib-only: rules run on already-parsed dict fixtures (no PyYAML); ported from `tools/control_audit.py`. A planted `audit_workflow_naive` that skips the SHA-pin check proves the action-pin rule has teeth.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `audit_workflow`, `CASES`.
- Planted-bad case: `action_pin_skipped`
- Oracle / proof target: Current proof target: `audit_workflow`, `CASES`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as a workflow-review fixture for permissions, pinned actions, untrusted inputs, and secret exposure in CI changes.
- Current outside reference: GitHub Actions security hardening guidance covers workflow permissions, untrusted input, and secret handling. <https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/ci_workflow_hardening_test_harness.py`; `python harnesses/security/ci_workflow_hardening_test_harness.py --self-test`; `python harnesses/security/ci_workflow_hardening_test_harness.py --list-scenarios`; `python -m unittest tests.security.test_ci_workflow_hardening_test_harness tests.security.test_ci_workflow_hardening_proof`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `security/diff_secret_gate`, `core/check_digit_identifier`, `core/lexical_date_canonicalization`.

## 75. Check-Digit Identifier Test Harness

- Name: Check-Digit Identifier Test Harness
- Path: `harnesses/core/check_digit_identifier_test_harness.py`
- Category: `core`
- Failure class: Self-checking-identifier checksum oracles, generalized via `ChecksumSpec` + a `SCHEMES` registry (`validate(scheme, identifier)`): DEA (faithful port of pharmacy-app `verify_dea_logic` — 2 letters + payload digits + mod-10 weighted check, ASCII-only guard so Unicode digits like `٠` are rejected, the F-05 fix), Luhn (mod-10), and ISBN-10 (mod-11, `X` check). The headline oracle `single_digit_corruption_sweep` enumerates every single-digit substitution of a valid sample and asserts detection. Luhn and ISBN-10 detect 100% of single-digit errors; the DEA checksum provably cannot (a ±5 swap on a doubled position leaves the units-digit check unchanged), so the harness asserts DEA reproduces exactly its derived blind set (`dea_expected_escapes`) rather than hide the weakness. A `validate_naive` that checks only length/charset proves the check-digit step is load-bearing. Also surfaces the DEA prefix→prescriber class. Numeric/Money (#23) has no checksum logic.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `validate`, `CASES`.
- Planted-bad case: `checksum_skipped`
- Oracle / proof target: Current proof target: `validate`, `CASES`.
- External testing pattern: check-digit identifier fixture and regression testing.
- Usage note: Use this as a data-entry fixture for check-digit validation, transposition detection, and identifier normalization.
- Current outside reference: ISO/IEC 7064 describes check-character systems for identifying data-entry errors; this harness maps the same general pattern. <https://www.iso.org/standard/31531.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/check_digit_identifier_test_harness.py`; `python harnesses/core/check_digit_identifier_test_harness.py --self-test`; `python harnesses/core/check_digit_identifier_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_check_digit_identifier_test_harness tests.core.test_check_digit_identifier_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/lexical_date_canonicalization`, `security/ci_workflow_hardening`, `security/diff_secret_gate`.

## 76. Diff Secret-Gate Test Harness

- Name: Diff Secret-Gate Test Harness
- Path: `harnesses/security/diff_secret_gate_test_harness.py`
- Category: `security`
- Failure class: A unified-diff-aware secret scanner. The novel oracle is **direction-awareness**: `scan_diff` reports a secret only on ADDED (`+`) lines with correct post-change line numbers — a secret on a REMOVED (`-`) line (e.g. a key being rotated out) does not trip the gate. `scan_line` matches secret tokens (AWS `AKIA…`, GitHub `ghp_`/`github_pat_`, PEM private-key blocks, Slack `xox[baprs]`, Google `AIza…`, generic `secret/token/password =` assignments) with an `allowlist secret` escape hatch. **Scope is secret tokens only** — PII (EMAIL/SSN/PHONE/CREDIT) is owned by PII/PHI Redaction (#62) and deliberately not duplicated. Ported from `tools/scan_staged.py` (PII regexes omitted). The planted `scan_diff_naive` scans every content line regardless of `+`/`-` and over-reports on a removed-secret diff, proving the direction logic has teeth. All fixture secrets are built by concatenation so the file does not trip its own gate.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `scan_diff`, `CASES`.
- Planted-bad case: `direction_blind`
- Oracle / proof target: Current proof target: `scan_diff`, `CASES`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as a pre-commit or PR fixture for catching synthetic secret patterns in diffs before public exposure.
- Current outside reference: GitHub secret scanning documentation describes identifying credentials in repository content. <https://docs.github.com/en/code-security/secret-scanning>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/diff_secret_gate_test_harness.py`; `python harnesses/security/diff_secret_gate_test_harness.py --self-test`; `python harnesses/security/diff_secret_gate_test_harness.py --list-scenarios`; `python -m unittest tests.security.test_diff_secret_gate_test_harness tests.security.test_diff_secret_gate_proof`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `security/ci_workflow_hardening`, `core/check_digit_identifier`, `core/lexical_date_canonicalization`.

## 77. Lexical Date Canonicalization Test Harness

- Name: Lexical Date Canonicalization Test Harness
- Path: `harnesses/core/lexical_date_canonicalization_test_harness.py`
- Category: `core`
- Failure class: Guards the data-corruption trap where a date string that parses fine but is not zero-padded silently breaks TEXT-column `ORDER BY` / range comparison: lexically `'2026-5-9' > '2026-10-01'`. Motivated by pharmacy-app `data.py` (`Inventory.exp_date TEXT NOT NULL`, `db_expired_inventory` does `WHERE exp_date < ? ORDER BY exp_date ASC`, and `_date_is_valid` accepts non-padded input via `strptime`). The headline invariant `lexical_matches_chronological`: for canonical dates, lexical sort == chronological sort; for a dataset containing a non-canonical string the two orders diverge, and `canonical_then_lexical_sort` restores agreement. `strict_is_valid` rejects parseable-but-non-canonical strings; the planted `lenient_is_valid` (mirroring the source `strptime` check) accepts `'2026-5-9'`, proving the strict rule is needed. Distinct from Time/DateTime (#20, round-trips only) and Date-Window Expiry (#42, calendar+SQL).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `canonicalize_iso`, `CANONICALIZE_CORPUS`, `CANON_CASES`.
- Planted-bad case: `mmdd_swap`, `two_digit_year_no_pivot`, `drop_timezone`
- Oracle / proof target: Current proof target: `canonicalize_iso`, `CANONICALIZE_CORPUS`, `CANON_CASES`, `SORT_CASES`.
- External testing pattern: lexical date canonicalization fixture and regression testing.
- Usage note: Use this as a date-normalization fixture for accepted formats, canonical output, invalid dates, and timezone-free lexical comparisons.
- Current outside reference: ISO 8601 defines standardized date and time representation; Python `datetime` exposes ISO parsing/formatting helpers. <https://www.iso.org/iso-8601-date-and-time-format.html> <https://docs.python.org/3/library/datetime.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/lexical_date_canonicalization_test_harness.py`; `python harnesses/core/lexical_date_canonicalization_test_harness.py --self-test`; `python harnesses/core/lexical_date_canonicalization_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_lexical_date_canonicalization_test_harness tests.core.test_lexical_date_canonicalization_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/check_digit_identifier`, `security/ci_workflow_hardening`, `security/diff_secret_gate`.

## Batch 16 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-16-ci-check-digit-diff-secret-lexical-date.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
