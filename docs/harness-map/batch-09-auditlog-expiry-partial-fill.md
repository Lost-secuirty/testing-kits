# Harness Map Batch 9

This file maps inventory entries #41-#43 in order: `pharmacy/auditlog_cap`, `pharmacy/expiry_window`, `pharmacy/partial_fill`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `pharmacy/auditlog_cap` = `legacy`, `pharmacy/expiry_window` = `legacy`, `pharmacy/partial_fill` = `legacy`.

## 41. Rotating-Capped Audit Log Test Harness

- Name: Rotating-Capped Audit Log Test Harness
- Path: `harnesses/pharmacy/auditlog_cap_test_harness.py`
- Category: `pharmacy`
- Failure class: Tests a DB-backed ring-buffer audit log with a hard row cap — a gap not covered by the Logging harness (format/sensitive-data) or the DB harness (generic CRUD). `AuditLogStore` prunes with `DELETE FROM AuditLog WHERE id NOT IN (SELECT id ... ORDER BY id DESC LIMIT cap)`. Scenarios: single write, newest-first retrieval, no-premature-prune at cap-1, cap triggers at cap+1 (exactly cap rows remain), newest rows retained after prune, idempotent overflow (3×cap inserts → always exactly cap rows), `LowCapAuditLog` (cap=3) prunes at 4th insert, auto-increment IDs not reset after prune, text-filter correct after prune cycle, integration export (10 rows), export file header format (`Pharmacy Audit Log Export`, `Generated:`, `Total entries:`, `---`), concurrent writes (10 threads × 5 rows → ≤ cap, no missing rows), `BuggyAuditLog` (skips DELETE) detected. Mock HTTP server on 19270.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Tests a DB-backed ring-buffer audit log with a hard row cap — a gap not covered by the Logging harness (format/sensitive-data) or the DB harness (generic CRUD). `AuditLogStore` prunes with `DELETE FROM AuditLog WHERE id NOT IN (SELECT id ... ORDER BY id DESC LIMIT cap)`. Scenarios: single write, newest-first retrieval, no-premature-prune at cap-1, cap triggers at cap+1 (exactly cap rows remain), newest rows retained after prune, idempotent overflow (3×cap inserts → always exactly cap rows), `LowCapAuditLog` (cap=3) prunes at 4th insert, auto-increment IDs not reset after prune, text-filter correct after prune cycle, integration export (10 rows), export file header format (`Pharmacy Audit Log Export`, `Generated:`, `Total entries:`, `---`), concurrent writes (10 threads × 5 rows → ≤ cap, no missing rows), `BuggyAuditLog` (skips DELETE) detected. Mock HTTP server on 19270.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a fixture for bounded audit-log rotation, retention caps, ordering, and write behavior under repeated events.
- Current outside reference: NIST SP 800-92 covers log management concepts including collection, retention, and analysis. <https://csrc.nist.gov/publications/detail/sp/800-92/final>
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/auditlog_cap_test_harness.py`; `python harnesses/pharmacy/auditlog_cap_test_harness.py --self-test`; `python -m unittest tests.pharmacy.test_auditlog_cap_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `pharmacy/expiry_window`, `pharmacy/partial_fill`.

## 42. Date-Window Expiry Alerting Test Harness

- Name: Date-Window Expiry Alerting Test Harness
- Path: `harnesses/pharmacy/expiry_window_test_harness.py`
- Category: `pharmacy`
- Failure class: Tests inventory expiration alerting — calendar boundary semantics combined with SQLite queries under a controllable clock. A gap distinct from the DateTime harness (pure date math) and DB harness (generic CRUD): the combined pattern of calendar arithmetic + parameterized SQL under an injected `today` string. `ExpiryStore` (in-memory SQLite): `expiring(within_days, today)` uses `<= cutoff` (inclusive); `expired(today)` uses `< today` (strictly exclusive). `DateWindowOracle` is the stdlib-only reference. Scenarios: today+30 in 30-day window (inclusive), today+31 NOT in window, already-expired in `expiring`, today NOT in `expired`, yesterday in `expired`, leap day 2024-02-29 as expired on 2024-03-01, month-end rollover (Jan 31 + 1 = Feb 01), year-end rollover (Dec 31 + 1 = Jan 01 next year), ASC sort by exp_date then name, empty result, `within_days=0`, 365-day scan, LIKE wildcard escape (`%`, `_`, `\` chars). Mock HTTP server on 19280.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Tests inventory expiration alerting — calendar boundary semantics combined with SQLite queries under a controllable clock. A gap distinct from the DateTime harness (pure date math) and DB harness (generic CRUD): the combined pattern of calendar arithmetic + parameterized SQL under an injected `today` string. `ExpiryStore` (in-memory SQLite): `expiring(within_days, today)` uses `<= cutoff` (inclusive); `expired(today)` uses `< today` (strictly exclusive). `DateWindowOracle` is the stdlib-only reference. Scenarios: today+30 in 30-day window (inclusive), today+31 NOT in window, already-expired in `expiring`, today NOT in `expired`, yesterday in `expired`, leap day 2024-02-29 as expired on 2024-03-01, month-end rollover (Jan 31 + 1 = Feb 01), year-end rollover (Dec 31 + 1 = Jan 01 next year), ASC sort by exp_date then name, empty result, `within_days=0`, 365-day scan, LIKE wildcard escape (`%`, `_`, `\` chars). Mock HTTP server on 19280.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a date-window regression fixture for alert timing and boundary handling; it does not validate medication safety decisions.
- Current outside reference: No clinical validation is claimed; this maps fixture-defined date-window alert behavior only.
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/expiry_window_test_harness.py`; `python harnesses/pharmacy/expiry_window_test_harness.py --self-test`; `python -m unittest tests.pharmacy.test_expiry_window_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `pharmacy/auditlog_cap`, `pharmacy/partial_fill`.

## 43. Partial-Fill Two-Phase Ledger Test Harness

- Name: Partial-Fill Two-Phase Ledger Test Harness
- Path: `harnesses/pharmacy/partial_fill_test_harness.py`
- Category: `pharmacy`
- Failure class: Tests pharmacy partial dispensing — a domain-specific open→resolve two-phase lifecycle not covered by the Idempotency harness (which targets HTTP idempotency keys and response artifacts). `PartialFillStore` (in-memory SQLite, `threading.Lock`): `add()` returns id; `list_open()` filters `resolved=0` newest-first; `count_open()` counts only unresolved; `resolve(pid)` issues `UPDATE ... WHERE id=? AND resolved=0` and returns `rowcount > 0`. Scenarios: fields correct on open, resolved disappears from `list_open`, True on first resolve, False on second (idempotent), False on nonexistent id, count=5, count=3 after 2 resolved, newest-first ordering, resolving #2 of 3 leaves #1 and #3, `AuditCapture` logs exactly once on True, `qty_owed=99` persisted correctly, concurrent race (2 threads, exactly one True + one False). `BuggyPartialFillStore` (always True) and `BuggyPartialFillStore2` (no WHERE filter) prove both failure directions. Mock HTTP server on 19290: `POST /partials` → 201, `GET /partials` → 200, `POST /partials/{id}/resolve` → 200/409.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Tests pharmacy partial dispensing — a domain-specific open→resolve two-phase lifecycle not covered by the Idempotency harness (which targets HTTP idempotency keys and response artifacts). `PartialFillStore` (in-memory SQLite, `threading.Lock`): `add()` returns id; `list_open()` filters `resolved=0` newest-first; `count_open()` counts only unresolved; `resolve(pid)` issues `UPDATE ... WHERE id=? AND resolved=0` and returns `rowcount > 0`. Scenarios: fields correct on open, resolved disappears from `list_open`, True on first resolve, False on second (idempotent), False on nonexistent id, count=5, count=3 after 2 resolved, newest-first ordering, resolving #2 of 3 leaves #1 and #3, `AuditCapture` logs exactly once on True, `qty_owed=99` persisted correctly, concurrent race (2 threads, exactly one True + one False). `BuggyPartialFillStore` (always True) and `BuggyPartialFillStore2` (no WHERE filter) prove both failure directions. Mock HTTP server on 19290: `POST /partials` → 201, `GET /partials` → 200, `POST /partials/{id}/resolve` → 200/409.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a ledger-style fixture for two-phase partial-fill state transitions, reconciliation, and rollback behavior.
- Current outside reference: No pharmacy workflow validation is claimed; this maps fixture-defined two-phase ledger behavior only.
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/partial_fill_test_harness.py`; `python harnesses/pharmacy/partial_fill_test_harness.py --self-test`; `python -m unittest tests.pharmacy.test_partial_fill_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `pharmacy/auditlog_cap`, `pharmacy/expiry_window`.

## Batch 9 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-09-auditlog-expiry-partial-fill.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
