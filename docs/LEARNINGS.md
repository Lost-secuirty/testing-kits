# Learnings - testing-kits

Append-only log of gotchas, fixes, API surprises, tool behavior, and verification notes. Keep entries dated, concise, and tied to evidence when possible.

## 2026-06-09 - core rule pack refresh

- Refreshed repository rules around strict verification, security, and a practical
  working agreement.
- Replaced older generic agent/security wording in AGENTS.md, CLAUDE.md, and SECURITY.md for this repo-specific rollout.
- Rollout branch/commit target: $branch.

## 2026-06-10 - harness proof audit

- Treat filesystem discovery plus `make proof` as the harness-count source of truth; committed generated status artifacts can be stale.
- `harnesses/core/complexity_test_harness.py` was real and tested but missing from the numbered inventory, which made the public count read 72 instead of the discovered 73.
- Windows self-test/report runs need explicit UTF-8 subprocess decoding; relying on the console code page can crash report generation on Unicode harness output.

## 2026-06-10 - reviewer polish wording

- Use "current proof baseline" or "checks passing" for the 73/73 status; do not describe the harness suite as total correctness proof.
- AI-authored tests need visible safe fixtures and planted-bad controls before their results are trusted.
- Pharmacy-domain harness docs must stay limited to fixture-defined software behavior, not clinical validation or production medication-safety claims.
- Run all-harness self-test/report commands sequentially; parallel `proof_audit` and `generate_report --check` runs can collide on local mock-server ports and create a false failure.

## 2026-06-10 - cleanup/update pass (docs, status convention, CI pins, dashboard deps)

- Correction to the 2026-06-09 entry: the unexpanded `$branch` was `codex-core-rule-pack-2026-06-09`, merged via PR #14 (commits eddc291, 4698ced, 529ffae, 333cd41). Lesson: expand shell variables before pasting into append-only logs.
- `HANDOFF.md` referenced a `codex/handoff` branch that no longer exists on local or origin; converted the file to a clearly-marked historical archive instead of deleting it.
- STATUS convention reconciled: `HARNESS_ROADMAP.md` and `README.md` say STATUS files are generated (`make report`) and CI-artifact-only, but `STATUS.json` was git-tracked while `STATUS.md` was ignored. Untracked `STATUS.json` and added it to `.gitignore`; reality pointed to the "not committed" convention.
- Action pins verified via `git ls-remote` peeled tag SHAs (network was available): `actions/checkout` v6.0.3 = `df4cb1c0...` (test.yml/scan.yml were already current; codeql.yml was on a v4-era pin and got bumped), `actions/setup-python` v6.2.0 = `a309ff8b...` (current), `actions/upload-artifact` v7.0.1 (current), `github/codeql-action` bumped from floating-`v4` SHA to exact v4.36.2 peeled SHA. Lightweight tags have no `^{}` entry in ls-remote output; annotated tags do — use the peeled SHA when present.
- `uvx zizmor` v1.25.2 on `.github/workflows/`: 3x artipacked (checkout without `persist-credentials: false`) and 1x high-confidence template-injection (`${{ github.base_ref }}` interpolated into a run block in scan.yml). Fixed all four (env-var indirection for the injection); re-run is clean.
- Pre-existing, untouched: `ruff check harnesses tests tools` reports 2288 findings (mostly UP006/UP045 typing modernization) in harness/test/tool code. `make lint` still exits 0 because the Makefile makes the ruff step non-blocking. Fixing these means touching harness code, so it needs its own pass with paired-test evidence.
- Verified on this pass: `make lint` exit 0, `make selftest` 73/73 green (47s), `make proof` full pass exit 0 (46s). Current PyPI floors verified 2026-06-10: streamlit 1.58.0, pandas 3.0.3, GitPython 3.1.50; dashboard floors raised to >=1.50 / >=2.2 / >=3.1.44.

## 2026-06-11 - shared core adoption + scanner-family note

- AGENTS.md working agreement / agent safety / source-of-truth swapped to the
  cross-repo shared core (this repo's wording was the template). "Research
  informs; the operator decides" moved under Operator rules; the audit fold-in
  restored "system/developer instructions" to the cannot-override list.
- The scan_staged.py family is deliberately divergent across repos: this repo
  (and Journal-and-findings) WARN on PII; the public repos BLOCK it. The
  variants are policy, not drift - read the module docstring before unifying.
  testing-kits is the de-facto upstream of the family.

## 2026-06-14 - Batch 0: teeth campaign foundation (branch feat/batch0-teeth-foundation)

- The old `proof_audit.py` "77/77 proven" was largely **keyword-based**: a harness
  counted as proven if its source merely contained markers like "safe"/"bad"/"buggy"
  (`embedded_controls`). That is string presence, not evidence a bug is caught.
  Hardened the gate: proven now requires a verified `TEETH` swap-check (correct
  oracle not flagged + every planted mutant caught + non-empty corpus), a paired
  unittest, and a green self-test. Scopes: `required` (declares `TEETH`), `pending`
  (no `TEETH` yet — counted, non-blocking), `legacy` (pharmacy, old soft gate). The
  `pending→required` design lets the gate be honest-strong without red-locking `main`.
- **Declaring `TEETH` is the opt-in to `required`** — there is no separate allowlist
  file to drift. New shared contract is `harnesses/_teeth.py` (pure stdlib, one level
  up so discovery's `harnesses/*/*.py` glob never treats it as a harness).
- **Direct-script execution gotcha:** harnesses run as `python harnesses/<cat>/x.py`,
  so `sys.path[0]` is the script dir, not repo root — a plain `from harnesses._teeth
  import ...` crashes. Every TEETH harness needs the `parents[2]` sys.path bootstrap
  (see `template/harness_template.py`). Verified empirically.
- **"All 77 self-test OK" was partly hollow:** several harnesses (e.g.
  core/idempotency) have no argparse/main, so `--self-test` is silently ignored and
  exits 0 as a no-op. The TEETH swap-check, not the self-test exit code, is the real
  signal. Each upgrade must add a genuine `Report`-based `--self-test`.
- **Grep for buggy twins over-counts GOLD:** core/datetime matched a "naive/buggy"
  string but has no oracle/twin/corpus and no real self-test — it is a class library
  needing a full upgrade, not a TEETH add. The anchoring agent correctly STOPPED and
  made zero edits rather than fabricate teeth.
- Anchored 9 GOLD harnesses with verified TEETH (additions-only, 479 insertions, 0
  deletions): check_digit_identifier, feature_flag, graphql, grpc_contract,
  idempotency, queue, tracing, ci_workflow_hardening, diff_secret_gate. `kind` is
  `oracle_swap` for predicate harnesses and `auditor` for finding-producers
  (feature_flag, grpc_contract, ci_workflow_hardening, diff_secret_gate).
- mutmut is **Linux/WSL-only** (boxed/mutmut#397 — confirmed: native Windows refuses).
  So the mandatory cross-platform gate is the stdlib swap-check (`make teeth` /
  `python tools/proof_audit.py`); `tools/mutmut_lane.py` + the CI `mutation-advisory`
  job (`continue-on-error`) are advisory and never block. The lane skip path and
  `--list` are verified on Windows; the live mutmut run is CI-validated-pending.
- Tooling installed via `uv` (PEP 735 `[dependency-groups] dev`, `uv.lock` committed,
  `.venv` gitignored): ruff 0.15.17, pytest 9.1.0, hypothesis, mutmut 3.6.0, deptry,
  zizmor 1.25.2. CI keeps its existing per-tool `pip install` pattern (the required
  path is pure-stdlib); migrating CI to uv is deferred. `[tool.mutmut]` uses the
  renamed `source_paths` key (3.6+), not `paths_to_mutate`.
- Verified this pass: `python tools/proof_audit.py --run-selftests` → 9 required (all
  teeth-verified), 60 pending, 8 legacy, **0 failing, exit 0**; full unittest suite
  **4420 tests OK** (153s). The proof_audit tool's own tests were rewritten in lockstep
  (the 3 keyword-era tests moved to the legacy path; added swap-check + real-repo
  required-path coverage).

## 2026-06-15 - Batch 1: real TEETH for 10 BRONZE/near-GOLD harnesses (branch feat/batch1-teeth)

- Flipped 10 harnesses pending → required (gate now **19 required / 50 pending / 8 legacy
  / 0 failing**): core/{api,cache,cli,config,contract,null_propagation,pagination,
  serialization,statemachine}, security/authz. Done in two waves via bounded agent
  workflows (3 near-GOLD, then 7 BRONZE); each agent's work was adversarially verified by
  a second agent plus an independent ground-truth re-run (teeth_check + --self-test + paired
  unittest + gate).
- The 3 "near-empty stubs" from the Batch 0 plan (authz/config/contract) were NOT empty —
  they were 420-660 line functional BRONZE harnesses. Survey the real file before trusting
  a plan's characterization.
- **Circularity is the failure mode the gate CANNOT catch.** `teeth_verified` only asserts
  prove(oracle)=False and prove(mutant)=True — a CIRCULAR prove() (comparing impl to the
  oracle at runtime) satisfies both and is vacuous. Every prove() here judges impl against a
  FROZEN literal corpus instead; confirmed by corrupting one literal and watching
  prove(oracle) flip False→True (authz). This is now the standard adversarial check.
- Reused each harness's existing correct logic as the oracle and planted faithful mutants
  modelling real bugs (RBAC fail-open / deny-precedence / ownership over-grant; env-not-
  overriding-file; 2**53+1 int→float serialization corruption; `>=` vs `>` page-boundary
  duplication; nondeterministic transition not flagged) — not trivial syntactic breaks.
- Non-blocking notes carried forward: core/cli has 2 mutants each caught by a single
  load-bearing corpus case (teeth hold; add redundancy when convenient); core/config's own
  EnvOverrideChecker has a dead `config_key` var (pre-existing; teeth use an independent
  helper); core/contract emits DEBUG logging during its socket smoke test (cosmetic).
- The network/post_path test (`test_network_test_harness`) is a PRE-EXISTING flaky
  localhost-timeout (2s) test — fires ~1/7 under CPU load on native Windows, green on Linux
  CI; tracked as a separate fix, NOT a Batch 1 regression.

## 2026-06-15 - Batch 2: real TEETH for 10 heavy-rewrite harnesses (branch feat/batch2-teeth)

- Flipped 10 more harnesses pending → required (gate now **29 required / 40 pending / 8
  legacy / 0 failing**, full suite 4539 OK): core/{db,scraper,fuzz,numeric,concurrency,
  error_path_leak,schema_evolution}, security/{supplychain,upload}, ai/agent_memory_context.
  Two waves (7 core, then 2 security + 1 ai) via bounded agent workflows + adversarial
  verify + independent ground-truth re-run.
- **numeric trap:** Python 3.12+ built-in `sum()` uses Neumaier compensation, so a "naive
  sum" mutant calling `sum()` would NOT diverge on 3.12-3.14 (a false-green teeth). The
  buggy mutant must accumulate with an explicit `+=` loop. (Same root cause as the Batch-6
  numeric fix.)
- **concurrency trap:** real thread races are flaky/non-deterministic — prove() must model
  the bad interleaving deterministically (forced ordering / single-thread sim), never spawn
  threads. Verified prove() is thread-free.
- **ai circularity trap (the failure the gate cannot catch):** agent_memory_context prove()
  judges retriever output against FROZEN `EXPECTED_RETRIEVED` id-tuples — never a model
  output / embedding / the oracle. Confirmed independent by corrupting a literal →
  prove(oracle) flips. This is exactly the answer-leak / stable-by-construction failure the
  DEP-TEST-KIT retro flagged for AI harnesses; the frozen-literal corpus avoids it.
- Corrected an over-claiming comment in the db injection mutant: the stacked
  `'); DROP TABLE users;--` payload may abort on the malformed first INSERT before the DROP
  runs — the bug is still caught (no clean row stored), just via a different path.
- Security harnesses got real teeth without a separate `test_*_proof.py`: the modern
  TEETH + paired `TestTeeth` + `assert_teeth` self-test supersedes the older proof-test
  convention.

## 2026-06-15 - Batch 3: real TEETH for 10 quick-win near-GOLD harnesses (branch feat/batch3-teeth)

- Flipped 10 more harnesses pending → required (gate now **39 required / 30 pending / 8
  legacy / 0 failing**, full suite **4595 OK** + 53 subtests): core/{statistical_rng_oracle,
  payments,canvas_scene_state,game_loop_simulation,iot_telemetry,browser_e2e,
  lexical_date_canonicalization}, security/cwe_kev_regression, ai/{agent_eval,drift_detection}.
  Built as two waves of 10 agents (wire, then adversarial verify) + my own independent
  ground-truth re-run (gate + teeth_check + --self-test + paired unittest + a literal-corruption
  non-circularity probe I ran myself on both AI harnesses).
- **All three `kind`s exercised:** `statistical` for distribution oracles — statistical_rng_oracle
  samples a SEEDED LcgRng and judges realized per-outcome proportions against a FROZEN literal
  proportion table (NOT recomputed from `TABLE.weight`); drift_detection judges a PSI detector
  against frozen drift/no-drift verdicts. `auditor` for finding-producers (cwe_kev, agent_eval).
- **ai circularity trap held again, and was isolation-tested:** agent_eval prove() judges a
  trajectory scorer against FROZEN verdict-string literals; a verifier monkeypatched the oracle
  to RAISE on any call and prove() still returned the right answer — decisive proof the verdict
  is driven by the frozen corpus, never a live oracle re-derivation. No model/LLM/embedding on
  any prove path.
- **Don't borrow the numeric/Neumaier framing where float drift isn't load-bearing.** The
  payments `float_drift_overcapture` mutant's docstring claimed CPython 3.12+ `sum()` Neumaier-
  compensation would mask the drift "so an explicit += loop is required" — but for its specific
  3×$0.10 case `sum()` and the `+=` loop give the SAME 0.30000000000000004, and the mutant is
  actually caught because it DISABLES the Decimal overcapture guard (banks 120 vs 100), not via
  drift. Corrected the comment to describe it honestly as a "money-in-float guard" defect. (The
  Neumaier trap is real and load-bearing in core/numeric — just not here.)
- cwe_kev `overbroad_xss` mutant (flags any `<`/`>`) diverges BOTH ways: false-positive on
  benign prose AND false-negative on an angle-bracket-free `onerror=` payload. Enriched the
  docstring; teeth unaffected (still caught, non-circular).
- **New flaky-test sibling observed:** under the full 4595-test run (high CPU contention) both
  `test_api_test_harness::TestMockServerIntegration::test_update_nonexistent_404` and the
  already-known `test_network_test_harness::test_post_echoes_path` failed on localhost
  mock-server socket timeouts; BOTH pass cleanly in isolation. Same root cause as the documented
  network flake (a short localhost timeout under load), neither in this batch's diff. The api one
  is newly noted here; a readiness-wait/retry fix for the mock-server tests should cover both.

## 2026-06-18 - docs mapping review / connector sequencing

- Permanent docs should not carry PR-specific verification metadata inside every harness dossier. The repeated `Docs touched:` lines made the map noisier and created doc-rot risk; keep batch closeout metadata in a dedicated closeout section or PR body instead.
- Do not mark a draft PR ready before completing the review-comment pass. Correct sequence: inspect changed files, read review comments, patch valid comments, re-check CI/status, then mark ready or merge.
- Verify the intended GitHub connector action before executing it. Repeated ready-for-review calls create timeline noise and can trigger review bots before the branch is actually ready.
- For docs-only mapping batches, review bots can still catch real documentation hygiene issues. Treat their comments as untrusted but useful input: inspect, accept only if the recommendation preserves repo rules, then patch narrowly.

## 2026-06-18 - large-doc connector boundary

- Do not full-replace large Markdown files from truncated connector output. GitHub file replacement requires the complete file body; if a fetch/blob view is truncated, replacing the file risks deleting unseen sections.
- Prefer modular batch files and small index updates over one central whole-file edit during active mapping work. Use line-window reads or another verified full-content source before any replacement.
- If a central doc needs consolidation, do it in a dedicated consolidation PR from a full local checkout or other non-truncated source, not during an active mapping batch.

## 2026-06-18 - Batch 7: stress/i18n/a11y/agentic/clock-skew TEETH

- Flipped 5 more harnesses pending -> required (gate now **59 required / 10 pending / 8 legacy / 0 failing**): core/{stress,i18n,a11y,clock_skew}, ai/agentic.
- Kept `core/stress` on its existing non-standard `stress_harness.py` filename for this PR. Renaming it would add import/path churn to a proof batch; the current TEETH work is already enough scope.
- Useful corpus shapes: stress proves corrected latency/error/weight accounting; i18n uses frozen Unicode edge cases rather than locale claims; a11y summarizes issue buckets instead of brittle full messages; agentic adds a real `--self-test` because the prior script could exit 0 without exercising checks; clock-skew keeps the legacy scenario list intact and adds a separate TEETH scenario list.

## 2026-06-18 - Phase 1.1: stress harness rename

- Renamed `harnesses/core/stress_harness.py` -> `stress_test_harness.py` (and the paired test to `test_stress_test_harness.py`), closing the only non-standard harness filename. `tests/core/test_stress_proof.py` keeps its name (the short name stays `stress`, so the proof-test path is unchanged) and the ratchet/cards key stays `core/stress`.
- The `if name == "stress_harness"` special-case in `tools/harness_registry.short_name()` was already redundant — the generic `_harness` suffix strip mapped it to `stress` anyway — so removing it changed no behavior; the proof-audit discovery test now exercises that fallback with a neutral `widget_harness.py` fixture instead.
- Why it mattered beyond tidiness: `tools/vacuity_gate.py` discovers harnesses by the `*_test_harness.py` glob, so the old name was invisible to it (it enumerated 68 of 69 non-legacy harnesses). The standard name makes stress discoverable, so vacuity now reaches all 69 (stress shows as UNMAPPED until it gets `VACUITY_TARGETS` in the Phase 3 rollout).

## 2026-06-19 - Phase 2E: dead-expression checker (advisory)

- Added `tools/dead_expr_checker.py` + `make dead_expr`: a stdlib-AST gate flagging bare side-effect-free expression statements — an `ast.Expr` whose value is `Name/Attribute/Compare/BinOp/BoolOp/UnaryOp/Subscript`. Excludes every `Constant` (docstrings, `...`, sentinel literals), `Call/Await/Yield/YieldFrom`, and the walrus `NamedExpr` — those are legitimate or may carry a side effect. Motivated by the two dead expressions the repo actually shipped (`core/mutation`'s `sum(...) + m.start()` and a since-removed one in `ai/llm_eval`) that the TEETH/proof/purity/circularity gates all stayed green on.
- ADVISORY per DP3: `tests/test_dead_expr_checker.py` asserts only the checker's own fixture behaviour plus that it analyses every harness without raising — it does not assert repo-wide cleanliness, so a dead line does not fail CI. It guards new harnesses (#78+) against introducing one; making it a required gate is a possible Phase 6 step once false-positive behaviour is confirmed across all harnesses and the five CI Pythons.
- Live run: **69/69 clean, 0 dead expressions.** The documented `core/mutation` dead expr was already removed in an earlier batch (the current `m.start()` occurrences are `code_part[:m.start()]` subscripts inside an assignment, not bare statements), and the crude null_propagation(4)/stress(6) exploration flags were false positives the precise AST checker (excluding `Constant`/`Call`) correctly ignores. Confirmed the checker still bites by injecting the real `sum(...) + m.start()` BinOp pattern into a temp file → DEAD_EXPR.
- dead_expr is a FLAT module scan and shares none of purity/circularity's call-graph machinery (`_dotted`/`_params`/`_Calls`/`_prove_target`/the BFS), so it stays self-contained; the shared-AST-helpers refactor remains a separate purity+circularity-only backlog item.
- Protected in `file_guard` (14 now) with the `.fileguard.json` bump landing in the same diff.
