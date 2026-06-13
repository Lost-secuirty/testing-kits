# Testing-Kits — Walk-Through
*A portable library of 73 small, pure-Python test harnesses, each one proving it can catch the bug it claims to catch · 2026-06-13*

## Bottom line
Testing-kits is a reference collection of 73 self-contained Python test harnesses — code that exercises a specific failure (a race condition, a biased random number generator, a prompt injection, a bad clinical calculation) and demonstrates the test catching it. It is deliberately a *library to port from*, not a product to deploy: a future project pulls the pattern it needs. Its distinguishing rule is that every harness must show both halves of the proof — a known-good input passes, and a deliberately broken ("planted-bad") input fails — so the tests themselves are tested.

## In plain terms (if you read nothing else)
Think of a workshop full of pre-built, labeled jigs. A jig is a fixture that holds a part the right way so you can check it quickly and repeatably. Each jig here checks one *kind* of mistake software can make. There are 73 of them, sorted into four drawers: general reliability, security, AI behavior, and pharmacy-style number-crunching.

The unusual part is how each jig earns trust. It is not enough for a jig to say "this part is fine." Each one also keeps a deliberately *defective* sample part on hand and shows that the jig correctly rejects it. A test that only ever says "pass" is worthless — it might be blind. So every harness here proves it can also say "fail" when it should. The repo's own motto is that the verification *is* the product.

Nothing here is installed into a running app or shipped to customers. It is a stockpile of proven patterns, meant to be copied into whatever a later project needs — hence "portable." Because each harness is a single small file with no outside dependencies, you can read it top to bottom and understand exactly what it does in a few minutes.

One honesty rule runs through everything, and it matters most in the pharmacy drawer: these harnesses prove only the rules written into their own test fixtures. The pharmacy ones look medical, but they make **no** claim about real patient safety, real medication dosing, or anything you could rely on in a clinic. They prove "the code does the arithmetic the fixture defined," and nothing more.

One quirk worth naming up front: this repo has no `docs/adr/` folder (Architecture Decision Records — dated notes explaining design choices). That is not a gap or an oversight. The repo simply predates that convention; it is history, not a design statement.

## Walk-through

### (1) What it is & why it exists (its role)
**Plain:** A portable test library — a curated shelf of small testing tools meant to be referenced or copied into future projects, not run as a live system. It exists to capture *how* to test tricky failure modes, with each tool carrying its own proof that it actually works.

**Technical:** A public, pure-stdlib Python harness collection (`pyproject.toml` declares `testing-kits` v0.2.0, `requires-python >=3.10`, **zero runtime dependencies**). `AGENTS.md` states the role directly: "small, inspectable harnesses with paired tests and no runtime dependency bloat." `PORTFOLIO.md` frames it as a "read-only-friendly showcase of testing and reliability patterns," and `README.md` is explicit that it is "a reusable reliability/testing-pattern library, not a production framework." The repo has **no `docs/adr/` directory** — confirmed by listing `docs/` — which is historical: it predates the ADR convention used in sibling repos, rather than a deliberate choice to omit design records. Significant lessons instead land in `docs/LEARNINGS.md` (per `AGENTS.md` git-workflow note).

### (2) How it's built
**Plain:** Plain Python, organized into four labeled drawers, with a matching test for every tool and a short list of one-word commands to run them. No frameworks, no installed packages — just readable files and the standard `make` + Python tooling.

**Technical:** Layout (from `README.md` and verified on disk):
- `harnesses/` holds the 73 harnesses in four categories — **core: 50, security: 8, ai: 7, pharmacy: 8** (counted on disk, excluding `__init__.py`; matches the README's "73 harnesses").
- `tests/` mirrors `harnesses/<category>/` with `test_*.py` files — **79 test files**, of which **5 are dedicated `*_proof.py` planted-bug files** (the rest pair API/CLI tests with the harness).
- `experiments/` is for in-progress harnesses, excluded from `make test` (only a `.gitkeep` present now).
- `template/harness_template.py` scaffolds new harnesses.
- `tools/` holds the machinery: `generate_report.py`, `proof_audit.py`, `harness_registry.py`, `rewrite_imports.py`, plus governance tools `control_audit.py` and `scan_staged.py`.
- `dashboard/` is an optional Streamlit viewer — explicitly the *only* part with third-party dependencies; the harnesses stay stdlib-only.

The `Makefile` exposes the command surface: `make test` (full `unittest` discovery), `make test-core/-security/-ai/-pharmacy` (`test-fast` aliases pharmacy, ~3s), `make selftest`, `make proof`, `make report`, `make lint` (`compileall` + `ruff` if installed), `make clean`. Ruff is the sole `dev` extra in `pyproject.toml`. A harness is a single file like `harnesses/core/statistical_rng_oracle_test_harness.py`: pure stdlib (`argparse`, `dataclasses`, `http.server`), a reference implementation, a deliberately broken counterpart (e.g. a `BiasedRng` class beside the real `LcgRng`), and a `--self-test` entry point.

### (3) How it works
**Plain:** Each tool can run itself in "self-test" mode: it feeds in a known-good case and confirms it passes, then feeds in a deliberately broken case and confirms it gets caught. A discovery tool finds all the harnesses automatically and runs them, so adding a new one wires it in without manual bookkeeping.

**Technical:** `tools/harness_registry.py` auto-discovers every real module under `harnesses/*/*.py` (skipping `__init__.py`) and, by naming convention, maps each to its paired test (`tests/<cat>/test_<stem>.py`) and optional proof file (`tests/<cat>/test_<name>_proof.py`). `run_self_test()` shells out to `python <harness> --self-test`, classifying the result OK / FAIL / SKIP / TIME. The self-test itself encodes the dual proof — e.g. in the RNG oracle, `_run_self_test()` asserts the good distribution passes, *and* fails loudly if the biased RNG is **not** caught (`"FAIL proof did not catch biased RNG"`), plus a seed-replay determinism check. `REVIEWER_QUICKSTART.md` warns to run sweeps sequentially because some harnesses spin up local mock servers and parallel runs can collide on ports (not a real failure).

### (4) How it's verified — the gates
**Plain:** Three layers. First, every tool proves itself (good passes, bad fails). Second, an auditor tool checks that *all* tools carry that proof and that none is silently missing its test. Third, automated checks run on every change in CI, plus a written rulebook for human reviewers and an automated check that the rulebook's required files are all present.

**Technical:**
- **Proof audit:** `tools/proof_audit.py` (`make proof`) discovers all harnesses and, for each, requires a paired unittest, an OK self-test (with `--run-selftests`), and both *safe* and *bad* control evidence — detected via marker word-lists (SAFE: safe/good/valid/pass/clean…; BAD: bad/buggy/planted/reject/fail/leak…) found in the harness, paired test, or proof file. Running it live now reports **`73/73 harnesses proven (all proven)`**, each tagged with its proof source (`embedded_controls`, `proof_file`, and/or `self_test_green`).
- **CI** (`.github/workflows/`, 8 workflows): `test.yml` runs `compileall`, `make test`, `make proof`, and `generate_report.py --check` across a Python **3.10–3.14** matrix, plus a wheel build/install smoke and `pip-audit` on the dashboard deps. Governance workflows: `repository-controls` (`controls.yml` → `control_audit.py` + pre-commit gates), `secret-pii-scan` (`scan.yml`), `Dependency Review`, `openssf-scorecard`, `CodeQL`, `main-artifacts`, and `release`.
- **Encoded governance:** `.github/control-policy.json` (schema v1, `repository_kind: python-tooling`, `visibility: public`) lists `required_files` and `required_workflows`; `control_audit.py` enforces their presence. Reviewer-facing rulebook lives in `docs/`: `AI_CODE_POLICY.md` ("AI-assisted code… is not trusted by default"), `PROOF_TEST_STANDARD.md` (the safe-passes / planted-bad-fails rule), `AI_FAILURE_MODE_MAP.md` (maps AI coding risks to harness areas with explicit limits), `REVIEWER_QUICKSTART.md`, and `AI_AUTHORED_TEST_AUDIT.md`. `STATUS.md`/`STATUS.json` are generated (git-ignored), so a stale report can't masquerade as canonical.

### (5) What it proves — and what it doesn't
**Plain:** It proves each tool can tell a good case from a planted-bad case for one specific kind of bug, and that all 73 currently do so under the repo's own checks. It does **not** prove any real application is correct, secure, or safe — and the medical-looking tools make no real-world safety claim at all.

**Technical:** Per `REVIEWER_QUICKSTART.md` and `PROOF_TEST_STANDARD.md`, the proven scope is narrow and stated plainly: the proof baseline discovers 73 real harnesses, each has a paired unittest, each currently passes `--self-test` under the proof audit, and each carries proof evidence (proof file, embedded controls, or self-test). The standard itself says: "Coverage can show code was exercised, but it does not prove the test would catch a real bug," and "This standard is a current proof baseline, not total correctness proof." `README.md` § "What this repo is not" and `PORTFOLIO.md` § "What this repo does not claim" enumerate the non-claims, with the pharmacy harnesses repeatedly fenced: fixture-defined software checks only — **no clinical validation, medication-safety certification, or pharmacy-grade correctness assurance.** The harness docstrings echo this (e.g. `clinical_calc_test_harness.py` tests calculators against independent reference formulas and plausibility bounds — a software oracle, not a medical authority; the RNG oracle says outright it is "not a casino certification tool").

## Honest limits (a skeptic's read)
- **It does not prove any target application is correct, secure, or safe.** Proof is per-harness, against fixtures the harness itself defines — not against your code.
- **Green is "good fixture passes, planted-bad fixture fails," not "bug-free."** The proof audit's safe/bad detection is partly **keyword-based** (marker word-lists in the harness/test/proof text), so it confirms control evidence *exists*, not that the controls are airtight.
- **Pharmacy/clinical harnesses carry zero real-world safety weight.** They prove fixture-defined arithmetic and bounds only — no clinical, dosing, or medication-safety claim. Respect that fence when porting.
- **AI-authored tests are untrusted by authorship** (`AI_CODE_POLICY.md`); trust comes only from the demonstrated pass/fail controls, not from "an AI wrote a passing test."
- **Concurrency/timing harnesses are environment-sensitive** (per `AI_FAILURE_MODE_MAP.md`); mutation operators and other probes are limited to what's implemented.
- **Self-test green ≠ CI green.** Local self-tests were confirmed via the proof audit here (73/73); the full CI matrix (Py 3.10–3.14) and external scanners (CodeQL, Scorecard) were **not** run in this review — treat their status as unverified for this document.
- **No `docs/adr/`** means design rationale is captured (if at all) in `docs/LEARNINGS.md` rather than dated decision records — fine for a historical reference library, thinner if you want the "why" behind a given harness.

## Glossary
- **Harness:** a small self-contained program that runs a target through a specific test scenario and reports pass/fail.
- **Fixture:** a fixed, known input set up for a test — here, a "safe" (good) one and a "planted-bad" (deliberately broken) one.
- **Planted-bad / negative control:** an intentionally wrong input used to confirm the test actually *catches* failures, not just rubber-stamps passes.
- **Self-test (`--self-test`):** a built-in mode where a harness runs its own good and bad cases and exits non-zero if anything is wrong.
- **Proof audit:** `tools/proof_audit.py` — checks every harness has a paired test, a passing self-test, and both safe and bad control evidence.
- **Pure stdlib / zero runtime dependencies:** uses only Python's built-in standard library; nothing extra to install to run the harnesses.
- **`unittest`:** Python's built-in testing framework; `make test` discovers and runs all `test_*.py` files.
- **Paired test:** the `test_*.py` file matched to each harness, covering its API and command-line behavior.
- **ADR (Architecture Decision Record):** a dated note explaining a design decision; this repo has none, by historical timing, not by choice.
- **CI (Continuous Integration):** automated checks (here, GitHub Actions workflows) that run on every push/PR.
- **control-policy.json / `control_audit.py`:** machine-readable list of required files and workflows, plus the tool that enforces they exist — governance encoded as code.
- **CodeQL / OpenSSF Scorecard / gitleaks:** third-party security scanners (code analysis, supply-chain hygiene score, secret detection) wired into CI.
- **Mutation probe:** a test that makes a small change to source code to check whether the existing tests notice — i.e. tests the tests.
- **RNG oracle:** a check that a random-number generator produces the expected distribution and replays deterministically from a seed.
- **Streamlit:** a Python web-app framework used only by the optional `dashboard/`; the harnesses themselves never depend on it.
