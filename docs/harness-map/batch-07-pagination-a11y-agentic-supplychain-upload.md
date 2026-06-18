# Harness Map Batch 7

This file maps inventory entries #31-#35 in order: `core/pagination`, `core/a11y`, `ai/agentic`, `security/supplychain`, `security/upload`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Current proof status is read from `cards/teeth_ratchet.json`: `core/pagination` = `required`, `core/a11y` = `required`, `ai/agentic` = `required`, `security/supplychain` = `required`, `security/upload` = `required`.

## 31. Pagination / Cursor Consistency Test Harness

- Name: Pagination / Cursor Consistency Test Harness
- Path: `harnesses/core/pagination_test_harness.py`
- Category: `core`
- Failure class: Tests pagination correctness over a mutable dataset. A thread-safe `BackingStore` is paginated two ways: `OffsetPaginator` (LIMIT/OFFSET) and `CursorPaginator` (keyset on a `(sort_key, id)` tiebreaker with an opaque base64 cursor). Proves the two classic offset bugs — a row deleted before the offset makes the next page SKIP a row, and a row inserted before the offset makes it RE-SHOW a row — and shows the cursor paginator is immune to both. Also covers unstable ordering without a tiebreaker, last-page/boundary cases (empty last page, exact-multiple, limit > dataset, limit ≤ 0 rejected), full-traversal reconciliation (every row seen exactly once), and cursor tamper-rejection (malformed, wrong structure, past-end).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_page`, `_EXPECTED_IDS`.
- Planted-bad case: `skip_boundary_item`, `duplicate_boundary_item`, `stuck_cursor`
- Oracle / proof target: Current proof target: `oracle_page`, `_EXPECTED_IDS`.
- External testing pattern: pagination / cursor consistency fixture and regression testing.
- Usage note: Use this as a pre-merge fixture for list APIs that support cursor or keyset pagination, especially when inserts, deletes, cursor tampering, or unstable ordering could create duplicate or skipped rows.
- Current outside reference: GraphQL's cursor pagination guidance describes cursor-based connection traversal and page info. <https://graphql.org/learn/pagination/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/pagination_test_harness.py`; `python harnesses/core/pagination_test_harness.py --self-test`; `python harnesses/core/pagination_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_pagination_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/a11y`, `ai/agentic`, `security/supplychain`, `security/upload`.

## 32. Accessibility (a11y) Test Harness

- Name: Accessibility (a11y) Test Harness
- Path: `harnesses/core/a11y_test_harness.py`
- Category: `core`
- Failure class: Static WCAG-flavored accessibility checks on HTML, parsed with stdlib `html.parser` (no bs4/lxml). Checkers: AltTextChecker (missing/empty/redundant alt), LabelChecker (orphan inputs/selects/textareas lacking `<label for>` / aria-label), HeadingOrderChecker (skipped levels, multiple h1), AriaChecker (invalid roles, missing required aria-* attrs, aria-hidden on focusable), ContrastChecker (WCAG sRGB linearization + relative-luminance + contrast-ratio math from scratch, AA 4.5:1 / 3:1 thresholds, parses #rrggbb/#rgb/rgb()), LangChecker, LinkTextChecker (“click here”/empty), TableChecker (data table missing th/scope). Explicitly static-only — catches ~30–40% of real a11y issues, no browser/runtime DOM.
- Logic shape: AND: static HTML parsing, issue bucketing, paired tests, and TEETH swap-check must all hold. NOT: missing alt text, unlabeled controls, ARIA misuse, and low contrast must not be filtered away.
- Good case: `oracle_a11y_audit` matches the frozen `A11Y_AUDIT_CORPUS` issue buckets for accessible HTML, missing alt, unlabeled input, skipped heading, invalid ARIA/focus-hidden control, low contrast, and missing language.
- Planted-bad case: `alt_blind_auditor`, `label_blind_auditor`, `contrast_blind_auditor`, and `aria_blind_auditor`.
- Oracle / proof target: Current proof target: `oracle_a11y_audit`, `A11Y_AUDIT_CORPUS`.
- External testing pattern: accessibility (a11y) fixture and regression testing.
- Usage note: Use this as a static HTML review aid for alt text, labels, heading order, ARIA attributes, contrast math, language, links, and table structure before relying on browser-based accessibility audits.
- Current outside reference: WCAG 2.2 defines accessibility success criteria for perceivable, operable, understandable, and robust interfaces. <https://www.w3.org/TR/WCAG22/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/a11y_test_harness.py`; `python harnesses/core/a11y_test_harness.py --self-test`; `python harnesses/core/a11y_test_harness.py --json`; `python harnesses/core/a11y_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_a11y_test_harness tests.core.test_a11y_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive accessibility coverage, browser/runtime DOM accessibility, assistive-technology behavior, or final harness maturity. This dossier maps current source, tests, and ratchet state; it is expected to change.
- Related harnesses: `core/pagination`, `ai/agentic`, `security/supplychain`, `security/upload`.

## 33. Agentic AI / Tool-Calling Test Harness

- Name: Agentic AI / Tool-Calling Test Harness
- Path: `harnesses/ai/agentic_test_harness.py`
- Category: `ai`
- Failure class: Tests AI-agent control-flow and tool-use correctness — the top 2026 agent failure modes — using a deterministic scripted `MockAgent` (no real LLM). A `ToolRegistry` of `ToolSchema`s (required/optional args, types, enums, dangerous flag) backs the checks. ToolCallFidelityTester flags hallucinated tool names, missing required args, wrong arg types, unknown extra args, and out-of-enum values (reporting a fidelity ratio). RunawayLoopDetector catches non-termination via round caps and repeated-call signatures. MultiTurnStateTester verifies state set early is used later; ArgSchemaDriftTester catches prompt-tool mismatch when a schema changes; PlanVsExecutionTester detects skipped/reordered steps; UnsafeToolUseTester flags dangerous tool calls made without a guard.
- Logic shape: AND: tool schema fidelity, loop detection, schema drift, plan-vs-execution, unsafe-tool guard checks, paired tests, and TEETH swap-check must all hold. NOT: known tool names alone must not be enough to pass a call.
- Good case: `oracle_agentic_audit` matches the frozen `AGENTIC_AUDIT_CORPUS` for valid, missing-arg, unknown-tool, repeated-loop, schema-drift, plan-order, unguarded-dangerous, and guarded-dangerous cases.
- Planted-bad case: `name_only_fidelity_auditor`, `loop_blind_auditor`, `schema_drift_blind_auditor`, and `unsafe_blind_auditor`.
- Oracle / proof target: Current proof target: `oracle_agentic_audit`, `AGENTIC_AUDIT_CORPUS`.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Usage note: Use this as a deterministic agent-control smoke test for tool schema fidelity, loop limits, multi-turn state, plan execution, and guarded dangerous-tool behavior without calling a live model.
- Current outside reference: AgentProcessBench and TRAJECT-Bench both frame agentic evaluation around tool-use process quality and trajectories, matching this harness's deterministic tool-call focus. <https://arxiv.org/html/2603.14465v1> <https://arxiv.org/html/2510.04550v1>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/agentic_test_harness.py`; `python harnesses/ai/agentic_test_harness.py --self-test`; `python harnesses/ai/agentic_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_agentic_test_harness tests.ai.test_agentic_proof`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, live-model quality, exhaustive tool schema coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state; it is expected to change.
- Related harnesses: `core/pagination`, `core/a11y`, `security/supplychain`, `security/upload`.

## 34. Supply-Chain / Build Reproducibility Test Harness

- Name: Supply-Chain / Build Reproducibility Test Harness
- Path: `harnesses/security/supplychain_test_harness.py`
- Category: `security`
- Failure class: Tests dependency and build integrity against a mock package registry. PinningChecker flags floating/wildcard version specifiers; IntegrityChecker verifies artifact sha256 against the lockfile with constant-time compare and rejects tampered artifacts; LockfileDriftChecker detects manifest-vs-lock divergence; NonexistentPackageChecker catches hallucinated dependencies (the “slopsquatting” failure) and warns on Levenshtein-1 typosquats; ReproducibleBuildChecker builds the same inputs twice and detects nondeterminism (embedded timestamp); KnownVulnChecker matches locked versions against a mock advisory range; TransitiveDepChecker resolves the dep tree and finds pin gaps and phantom deps.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_admit`, `EXPECTED_VERDICTS`, `SUPPLY_CORPUS`.
- Planted-bad case: `skip_integrity`, `allow_unpinned`, `allow_typosquat`
- Oracle / proof target: Current proof target: `oracle_admit`, `EXPECTED_VERDICTS`, `SUPPLY_CORPUS`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as a docs-and-CI fixture for dependency pinning, artifact integrity, lockfile drift, typosquat or nonexistent-package checks, known-vulnerability matching, and reproducible-build regressions.
- Current outside reference: SLSA describes supply-chain security levels and build provenance concepts. <https://slsa.dev/spec/v1.0/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/supplychain_test_harness.py`; `python harnesses/security/supplychain_test_harness.py --self-test`; `python harnesses/security/supplychain_test_harness.py --list-scenarios`; `python -m unittest tests.security.test_supplychain_test_harness`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `security/upload`, `core/pagination`, `core/a11y`, `ai/agentic`.

## 35. File Upload / Decompression-Bomb Test Harness

- Name: File Upload / Decompression-Bomb Test Harness
- Path: `harnesses/security/upload_test_harness.py`
- Category: `security`
- Failure class: Tests file-ingestion safety — a classic silent-DoS / type-confusion surface. MultipartParser parses `multipart/form-data` (multiple fields + file parts, boundary-in-content, missing trailing boundary, CRLF, empty parts, truncated body). DecompressionBombChecker decompresses gzip/zlib/zip under a hard output cap and max compression-ratio, rejecting bombs before memory exhaustion, with a nested-zip depth limit. ContentTypeSniffer compares declared Content-Type against magic bytes (PNG/GIF/PDF/ZIP/JPEG), enforces an allow-list and `nosniff`. SizeLimitChecker stops streaming reads early at the limit; FilenameSanitizer rejects path-traversal, null bytes, absolute paths, and Windows reserved names; PartialStreamTester detects truncated uploads.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_validate`, `EXPECTED_VERDICTS`, `UPLOAD_CORPUS`.
- Planted-bad case: `trusts_declared_content_type`, `path_traversal_filename`, `skips_size_cap`
- Oracle / proof target: Current proof target: `oracle_validate`, `EXPECTED_VERDICTS`, `UPLOAD_CORPUS`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as an ingestion regression fixture for upload endpoints or file-processing jobs, covering type sniffing, size caps, filename sanitization, multipart parsing, truncated streams, and decompression-bomb limits.
- Current outside reference: OWASP File Upload guidance covers extension, content-type, size, and decompression-bomb style controls. <https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/upload_test_harness.py`; `python harnesses/security/upload_test_harness.py --self-test`; `python harnesses/security/upload_test_harness.py --list-scenarios`; `python -m unittest tests.security.test_upload_test_harness`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `security/supplychain`, `core/pagination`, `core/a11y`, `ai/agentic`.

## Batch 7 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-07-pagination-a11y-agentic-supplychain-upload.md`
- `docs/harness-map/README.md`

Scope note: this batch file originated in a docs-only mapping PR. The current teeth-campaign update changes `core/a11y` and `ai/agentic` source/tests/cards and refreshes this dossier to the new required ratchet state.
