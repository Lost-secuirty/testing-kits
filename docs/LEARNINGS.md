# Learnings - testing-kits

Append-only log of gotchas, fixes, API surprises, tool behavior, and verification notes. Keep entries dated, concise, and tied to evidence when possible.

## 2026-06-09 - core rule pack refresh

- Refreshed repo rules from the cross-repo core pack: strict verification/security from Inbound-health-care/Health-Prototype plus the lighter practical working agreement from Lostsoulfs/My-sons-game.
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
