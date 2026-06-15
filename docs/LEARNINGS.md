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
