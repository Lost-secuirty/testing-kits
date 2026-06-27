# Harness Map Batch 8

This file maps inventory entries #36-#40 in order: `security/appsec`, `pharmacy/srs`, `pharmacy/clinical_calc`, `pharmacy/lockout`, `pharmacy/backup_restore`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `security/appsec` = `required`, `pharmacy/srs` = `legacy`, `pharmacy/clinical_calc` = `legacy`, `pharmacy/lockout` = `legacy`, `pharmacy/backup_restore` = `legacy`.

## 36. App-Security Test Harness (SSRF / Deserialization / JWT / XXE)

- Name: App-Security Test Harness (SSRF / Deserialization / JWT / XXE)
- Path: `harnesses/security/appsec_test_harness.py`
- Category: `security`
- Failure class: Complements the injection-focused Security harness #6 by covering the OWASP-heavy classes most over-represented in AI-generated code, each with a vulnerable-vs-hardened demonstration. SSRFChecker blocks private/loopback/link-local/metadata ranges and `file://`/`gopher://` via the `ipaddress` module; DeserializationChecker detects dangerous pickle opcodes, PyYAML `!!python/object`, and Java serialization magic by signature (never unpickling untrusted data; insecure deserialization is ~2.74× more common in AI code); JWTChecker catches `alg:none`, HS/RS algorithm confusion, expired `exp`, and `iss`/`aud` mismatch with `hmac`-verified HS256; OpenRedirectChecker, MassAssignmentChecker (allow-list binder), and XXEChecker (DOCTYPE/ENTITY detection without resolution) round it out.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `ssrf_oracle`, `SSRF_CORPUS`.
- Planted-bad case: `misses_metadata`, `substring_blocklist`
- Oracle / proof target: Current proof target: `ssrf_oracle`, `SSRF_CORPUS`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as a compact application-security fixture when reviewing SSRF, deserialization, JWT, XXE, and related request-handling controls before wider dynamic testing.
- Current outside reference: OWASP WSTG covers web application security testing areas including input validation, SSRF-adjacent issues, and API behavior. <https://owasp.org/www-project-web-security-testing-guide/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/appsec_test_harness.py`; `python harnesses/security/appsec_test_harness.py --self-test`; `python harnesses/security/appsec_test_harness.py --list-scenarios`; `python -m unittest tests.security.test_appsec_test_harness`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `pharmacy/srs`, `pharmacy/clinical_calc`, `pharmacy/lockout`, `pharmacy/backup_restore`.

## 37. Spaced-Repetition (SRS) Test Harness

- Name: Spaced-Repetition (SRS) Test Harness
- Path: `harnesses/pharmacy/srs_test_harness.py`
- Category: `pharmacy`
- Failure class: Tests the SM-2 spaced-repetition algorithm used by the pharmacy PTCB-prep app: initial-state outputs, interval ladder (1→6→EF-scaled), incorrect-answer reset (interval=0, reps=0), ease floor at 1.3, monotonicity invariants (ease never decreases on correct, interval grows for reps≥2), convergence (20 correct cycles → interval > 100 days), ease upper bound (1000 correct → ease ≤ 105), junk-input fallback, and full DB round-trip (sm2_update → upsert → retrieve → values match). Also tests `calculate_weight`: overdue path (cap 50), not-yet-due path, monotone with increasing days_since, and legacy-NULL fallback. `MockMasteryStore` uses in-memory SQLite with thread-safe locking.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Tests the SM-2 spaced-repetition algorithm used by the pharmacy PTCB-prep app: initial-state outputs, interval ladder (1→6→EF-scaled), incorrect-answer reset (interval=0, reps=0), ease floor at 1.3, monotonicity invariants (ease never decreases on correct, interval grows for reps≥2), convergence (20 correct cycles → interval > 100 days), ease upper bound (1000 correct → ease ≤ 105), junk-input fallback, and full DB round-trip (sm2_update → upsert → retrieve → values match). Also tests `calculate_weight`: overdue path (cap 50), not-yet-due path, monotone with increasing days_since, and legacy-NULL fallback. `MockMasteryStore` uses in-memory SQLite with thread-safe locking.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a fixture-defined scheduler regression check for spaced-repetition queue behavior; it does not validate clinical learning outcomes.
- Current outside reference: No clinical authority is claimed; this maps fixture-defined spaced-repetition software behavior only.
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/srs_test_harness.py`; `python harnesses/pharmacy/srs_test_harness.py --self-test`; `python -m unittest tests.pharmacy.test_srs_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `pharmacy/clinical_calc`, `pharmacy/lockout`, `pharmacy/backup_restore`.

## 38. Clinical Calculator Test Harness

- Name: Clinical Calculator Test Harness
- Path: `harnesses/pharmacy/clinical_calc_test_harness.py`
- Category: `pharmacy`
- Failure class: Tests medical calculators as a **patient-safety-critical** domain, with independent reference implementations as oracles. BSA Mosteller: formula identity verified to 2 dp, plausibility bounds [0.10, 4.00] m², strict monotonicity in height and weight. Cockcroft-Gault CrCl: formula identity, female factor exactly 0.85×, strictly decreasing with age and SCr, age boundary (141 raises ValueError). Peds dose: dimensional consistency both legs. Days supply: floor division convention (qty=31, daily=3 → 10). Insulin: priming-waste subtracted (F-06 fix), 3650-day cap. Cross-calculator: doubling weight increases both BSA and CrCl.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Tests medical calculators as a **patient-safety-critical** domain, with independent reference implementations as oracles. BSA Mosteller: formula identity verified to 2 dp, plausibility bounds [0.10, 4.00] m², strict monotonicity in height and weight. Cockcroft-Gault CrCl: formula identity, female factor exactly 0.85×, strictly decreasing with age and SCr, age boundary (141 raises ValueError). Peds dose: dimensional consistency both legs. Days supply: floor division convention (qty=31, daily=3 → 10). Insulin: priming-waste subtracted (F-06 fix), 3650-day cap. Cross-calculator: doubling weight increases both BSA and CrCl.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a fixture-defined calculator regression check for known inputs, rounding, boundaries, and error handling; it does not validate medical advice or clinical correctness.
- Current outside reference: No clinical validation is claimed; this maps fixture-defined calculator behavior only.
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/clinical_calc_test_harness.py`; `python harnesses/pharmacy/clinical_calc_test_harness.py --self-test`; `python -m unittest tests.pharmacy.test_clinical_calc_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `pharmacy/srs`, `pharmacy/lockout`, `pharmacy/backup_restore`.

## 39. Temporal PIN Lockout Test Harness

- Name: Temporal PIN Lockout Test Harness
- Path: `harnesses/pharmacy/lockout_test_harness.py`
- Category: `pharmacy`
- Failure class: Tests time-windowed PIN lockout — a gap not covered by the AuthZ (RBAC) or Rate-Limiting (throughput) harnesses. `FakeClock` (injectable, `now()` + `advance()`) enables deterministic boundary testing without real sleeps. `LockoutManager` tracks `{username: {count, locked_since}}` with a `threading.Lock` and configurable `threshold`/`lockout_seconds`. Covers: first attempt never locked, THRESHOLD-1 still permitted, exact threshold locks, t=299s still locked (exclusive), t=300s released (inclusive), counter resets on expiry, successful attempt resets before threshold, per-user isolation (locking A does not affect B), ±1s boundary precision, `BuggyLockoutManager` (never unlocks) detected, configurable thresholds (1 and 5), and concurrent attempts → lockout fires exactly once. Mock HTTP server on 19250: `POST /login` → 200/401/423.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Tests time-windowed PIN lockout — a gap not covered by the AuthZ (RBAC) or Rate-Limiting (throughput) harnesses. `FakeClock` (injectable, `now()` + `advance()`) enables deterministic boundary testing without real sleeps. `LockoutManager` tracks `{username: {count, locked_since}}` with a `threading.Lock` and configurable `threshold`/`lockout_seconds`. Covers: first attempt never locked, THRESHOLD-1 still permitted, exact threshold locks, t=299s still locked (exclusive), t=300s released (inclusive), counter resets on expiry, successful attempt resets before threshold, per-user isolation (locking A does not affect B), ±1s boundary precision, `BuggyLockoutManager` (never unlocks) detected, configurable thresholds (1 and 5), and concurrent attempts → lockout fires exactly once. Mock HTTP server on 19250: `POST /login` → 200/401/423.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a workflow regression check for PIN retry throttling, lockout windows, and unlock timing in the fixture model.
- Current outside reference: NIST SP 800-63B documents authenticator and verifier guidance, including throttling and retry controls. <https://pages.nist.gov/800-63-4/sp800-63b.html>
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/lockout_test_harness.py`; `python harnesses/pharmacy/lockout_test_harness.py --self-test`; `python -m unittest tests.pharmacy.test_lockout_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `pharmacy/srs`, `pharmacy/clinical_calc`, `pharmacy/backup_restore`.

## 40. Backup / Restore Lifecycle Test Harness

- Name: Backup / Restore Lifecycle Test Harness
- Path: `harnesses/pharmacy/backup_restore_test_harness.py`
- Category: `pharmacy`
- Failure class: Tests SQLite online backup/restore lifecycle — a gap not covered by the generic DB harness which tests only CRUD/transactions. Uses `sqlite3.Connection.backup()` (WAL-safe, transactional). Scenarios: magic bytes at offset 0, backup immediately readable with all source tables, data-faithful row comparison, timestamp-pattern filename, `db_list_backups()` newest-first, full round-trip (snapshot → mutate → restore → mutation gone), atomic restore (live DB readable after), non-existent path raises `OperationalError`/`OSError`, empty (schema-only) DB, NULL columns survive intact, WAL mode does not corrupt, unlistable directory returns `[]` (no raise), corrupt `.db` bytes raise on restore without overwriting live DB. All paths in `tempfile.mkdtemp()` isolated directories.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Tests SQLite online backup/restore lifecycle — a gap not covered by the generic DB harness which tests only CRUD/transactions. Uses `sqlite3.Connection.backup()` (WAL-safe, transactional). Scenarios: magic bytes at offset 0, backup immediately readable with all source tables, data-faithful row comparison, timestamp-pattern filename, `db_list_backups()` newest-first, full round-trip (snapshot → mutate → restore → mutation gone), atomic restore (live DB readable after), non-existent path raises `OperationalError`/`OSError`, empty (schema-only) DB, NULL columns survive intact, WAL mode does not corrupt, unlistable directory returns `[]` (no raise), corrupt `.db` bytes raise on restore without overwriting live DB. All paths in `tempfile.mkdtemp()` isolated directories.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a lifecycle fixture for backup creation, restore validation, corruption handling, and retention behavior in non-production data flows.
- Current outside reference: NIST contingency planning guidance frames backup, recovery, and restoration as software/system resilience activities. <https://csrc.nist.gov/publications/detail/sp/800-34/rev-1/final>
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/backup_restore_test_harness.py`; `python harnesses/pharmacy/backup_restore_test_harness.py --self-test`; `python -m unittest tests.pharmacy.test_backup_restore_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `pharmacy/srs`, `pharmacy/clinical_calc`, `pharmacy/lockout`.

## Batch 8 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-08-appsec-srs-clinical-lockout-backup.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
