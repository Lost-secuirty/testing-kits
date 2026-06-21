# Testing-Kits — Walk-Through

*A portable library of 92 small, pure-Python test harnesses. Verification patterns are the product; the repo is not an application, framework, or certification suite.*

## Bottom line

Testing-kits is a reference collection of **92 self-contained Python test harnesses**. Each harness demonstrates one failure mode with a known-good case and a planted-bad case. It is deliberately a library to read from, learn from, and port from, not a product to deploy.

The current proof model is a ratchet, not a blanket claim that all 92 are proven in the same way. The TEETH campaign distinguishes **required**, **pending**, and **legacy** harnesses. As of the current Batch 10 teeth state, the snapshot is **84 required / 0 pending / 8 legacy / 0 failing**. Re-run `make proof` before treating that as a fresh release claim.

## In plain terms

Think of a workshop full of pre-built, labeled jigs. A jig is a fixture that holds a part the right way so you can check it quickly and repeatably. Each jig here checks one kind of software mistake. The drawers are general reliability, security, AI behavior, and pharmacy-style software checks.

The unusual part is how each jig earns trust. It is not enough for a jig to say "this part is fine." Each one also keeps a deliberately defective sample part on hand and shows that the jig rejects it. A test that only ever says "pass" may be blind. The repo is organized around proving that the tests can also say "fail" when they should.

Nothing here is installed into a running app or shipped to customers. It is a stockpile of portable verification patterns. Because each harness is a single Python file with no runtime dependencies, a reviewer can read the harness and paired tests directly.

One honesty rule runs through everything, especially the pharmacy-domain harnesses: these harnesses prove only the rules written into their own test fixtures. The pharmacy files may look medical, but they make **no** claim about real patient safety, real medication dosing, clinical validation, or medication-safety certification. They prove fixture-defined software behavior only.

## Walk-through

### 1. What it is and why it exists

**Plain:** A portable test library: a curated shelf of small testing tools meant to be referenced or copied into future projects. It exists to capture how to test tricky failure modes, with each tool carrying a visible safe case and planted-bad case.

**Technical:** A public, pure-stdlib Python harness collection. `AGENTS.md` states the role as small, inspectable harnesses with paired tests and no runtime dependency bloat. `README.md` frames it as a reusable reliability/testing-pattern library, not a production framework. The repo has no `docs/adr/` directory; significant lessons are captured in `docs/LEARNINGS.md` instead.

### 2. How it is built

**Plain:** Plain Python, organized into labeled categories, with matching tests and short commands to run them. The optional dashboard is separate and dependency-backed; the harnesses themselves stay standard-library-only.

**Technical:** Layout:

- `harnesses/` holds the 92 harnesses across `core`, `security`, `ai`, and `pharmacy`.
- `tests/` mirrors `harnesses/<category>/` with paired `test_*.py` files.
- `experiments/` is for in-progress harnesses and is excluded from `make test`.
- `template/harness_template.py` scaffolds new harnesses.
- `tools/` holds report, proof, registry, rewrite, control-audit, and staged-scan utilities.
- `dashboard/` is an optional Streamlit viewer; it is explicitly separate from the stdlib harness collection.

The Makefile exposes the reviewer command surface: `make test`, focused category test targets, `make selftest`, `make proof`, `make report`, `make lint`, and `make clean`.

### 3. How it works

**Plain:** A harness runs a known-good case and a planted-bad case. The good case should pass. The bad case should fail for the intended reason.

**Technical:** `tools/harness_registry.py` discovers harness modules under `harnesses/*/*.py` and maps each to its paired test by naming convention. `tools/proof_audit.py` checks inventory/proof state and can run harness self-tests. The TEETH-upgraded harnesses declare a `TEETH` contract that points to a correct oracle, planted mutants, and a deterministic proof predicate.

### 4. How it is verified — the gates

**Plain:** Verification happens in layers. First, a harness demonstrates good/pass and bad/fail behavior. Second, the proof audit checks harness status across the inventory. Third, CI and governance checks verify that required files and workflows remain present.

**Technical:**

- **TEETH proof audit:** `tools/proof_audit.py` / `make proof` reports harnesses by status:
  - `required` — TEETH declared and verified;
  - `pending` — counted but not yet ratcheted into TEETH;
  - `legacy` — pharmacy-domain soft-gate status.
- **Current loaded proof snapshot:** Batch 10 records **84 required / 0 pending / 8 legacy / 0 failing**. This is a current proof baseline from loaded repo state, not a permanent claim.
- **CI/governance:** `.github/control-policy.json` lists required files and workflows. `tools/control_audit.py` enforces the control-policy presence checks. `STATUS.md` and `STATUS.json` are generated artifacts, not canonical committed status.

### 5. What it proves — and what it does not

**Plain:** It proves that each harness has a fixture-defined safe/bad test pattern and that TEETH-required harnesses catch their planted mutants under the current tooling. It does not prove any real application is correct, secure, or safe.

**Technical:** `docs/PROOF_TEST_STANDARD.md` defines the current proof baseline: safe fixtures pass, planted-bad fixtures fail, and the result is not total correctness proof. The TEETH campaign is stronger than the older marker-based proof language, but it is still scoped to fixture-defined behavior.

## Honest limits

- **It does not prove any target application is correct, secure, or safe.** Proof is per-harness and fixture-defined.
- **92 is inventory size, not a blanket proof status.** Current proof status must preserve the `required` / `pending` / `legacy` distinction.
- **Green means the current gate accepted the fixture-defined evidence.** It does not mean bug-free.
- **Pharmacy-domain harnesses carry zero real-world safety weight.** They are not clinical validation, medication-safety certification, production pharmacy assurance, or dosing authority.
- **AI-authored tests are untrusted by authorship.** Trust comes from demonstrated safe/bad controls, not from who or what wrote the test.
- **Concurrency/timing harnesses are environment-sensitive.** Deterministic controls are preferred where possible.
- **Self-test green is not CI green.** Do not claim CI passed unless CI actually reports green for the relevant commit.

## Glossary

- **Harness:** a small self-contained program that runs a target through a specific test scenario and reports pass/fail.
- **Fixture:** a fixed, known input set up for a test.
- **Planted-bad / negative control:** an intentionally wrong input or implementation used to confirm the test catches failures.
- **Self-test (`--self-test`):** a built-in mode where a harness runs its own good and bad cases.
- **Proof audit:** `tools/proof_audit.py`, the inventory/proof-status checker behind `make proof`.
- **TEETH:** the hardened proof contract for required harnesses: correct oracle not flagged, planted mutants caught, non-empty corpus.
- **Required / pending / legacy:** proof-ratchet states used so current proof claims are precise.
- **Pure stdlib / zero runtime dependencies:** the harnesses use only Python's standard library.
- **Paired test:** the `test_*.py` file matched to each harness.
- **CI:** automated GitHub Actions checks.
- **control-policy.json / `control_audit.py`:** machine-readable required-file/workflow policy and its checker.
- **Mutation probe:** a test that makes a small behavior change to check whether the tests notice.
- **RNG oracle:** a check that a random-number generator produces an expected distribution and replays deterministically from a seed.
- **Streamlit:** the framework used only by the optional dashboard; harnesses do not depend on it.
