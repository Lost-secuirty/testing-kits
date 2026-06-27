# Contributing to testing-kits

Thanks for your interest. This repo is a library of small, inspectable, pure-standard-library
test harnesses. Each harness demonstrates one failure mode with a known-good case and a
planted-bad case, and proves — under the repo's gate suite — that it catches the planted bug
without flagging the correct oracle. Contributions are judged on that discipline, not on volume.

## Read first

`AGENTS.md` and `CLAUDE.md` are the universal instruction sources for both humans and agents;
read them together with `SECURITY.md` before proposing changes. `docs/LEARNINGS.md` records
recurring gotchas worth knowing before you repeat old work. New contributors should also skim
`docs/REVIEWER_QUICKSTART.md` and `docs/READER_LEVELS.md`.

This is a **public** repository: no secrets, tokens, credentials, private data, real PHI, or
sensitive examples belong in commits, fixtures, generated artifacts, issues, or PRs. Use
synthetic or redacted examples only.

## Local setup

No runtime dependencies are needed to run the harness collection — it is pure standard library.

```bash
git clone https://github.com/Lost-secuirty/testing-kits
cd testing-kits
python --version            # 3.10–3.14 supported
make test                  # full unittest discovery
make proof                 # harness proof audit (TEETH swap-check)
```

On Windows, where `make` is unavailable, run the underlying commands directly:

```bash
python -m unittest discover -s tests -t . -p "test_*.py"
python tools/proof_audit.py --run-selftests
python tools/generate_report.py --check
```

Optional developer tooling (ruff, pytest, mutmut, etc.) is declared as a PEP 735
`dev` dependency group in `pyproject.toml`. Install it with `uv sync` (uv supports
PEP 735 dependency groups natively); standalone `pip` supports it from version 25.1 via
`pip install --group dev`. It is never required for the core, pure-stdlib path.

## The harness contract

Every new **required** harness must:

- Live in `harnesses/<category>/<name>_test_harness.py`, pure standard library, zero runtime deps.
- Pair a known-good oracle with at least one planted `Mutant`, plus a pure, deterministic
  `prove(impl) -> bool` that judges a frozen corpus (no clock, RNG, network, or filesystem; it
  must not call its own oracle at runtime).
- Declare a module-level `TEETH = Teeth(...)` contract and ship a paired `unittest` under
  `tests/<category>/`.
- Pass the full gate suite locally before you push:

  ```bash
  make test && make proof && make vacuity && make purity && make circularity \
    && make corpus_size && make fragility && make dead_expr && make guard && make canary && make lint
  ```

  On Windows without `make`, each target maps to a direct command:

  ```bash
  python -m unittest discover -s tests -t . -p "test_*.py"   # test
  python tools/proof_audit.py --run-selftests                # proof
  python tools/vacuity_gate.py                               # vacuity
  python tools/prove_purity_checker.py                       # purity
  python tools/prove_circularity_checker.py                  # circularity
  python tools/corpus_size_checker.py                        # corpus_size
  python tools/fragility_checker.py                          # fragility
  python tools/dead_expr_checker.py                          # dead_expr
  python tools/file_guard.py                                 # guard
  python tools/gate_canary.py                                # canary
  python -m compileall -q harnesses tests tools              # lint (+ ruff if installed)
  ```

- Regenerate harness cards with `python cards/harness_card.py --write --update-ratchet`
  (generated — do not hand-edit), and update `HARNESS_INVENTORY.md`, `docs/HARNESS_MAP.md`,
  and the relevant counts.

If a harness cannot satisfy a gate honestly, it stays `pending` (no `TEETH`) rather than
weakening a gate. **Never weaken a gate to make a change pass.**

## Pull request workflow

- Work on a feature branch; never commit to `main`. Open PRs as **draft**; a maintainer makes
  every merge call.
- Keep each PR scoped to one unit. Do not mix harness code with workflow, dependency, dashboard,
  or generated-status changes.
- All required CI checks (across Python 3.10–3.14) plus the bot review must be green, and review
  threads resolved, before a PR can merge.
- Use the `## Verification` section of the PR template to list the exact commands you ran (and any
  you did not, with the reason). Do not write vague claims such as "tests passed".

### Commit messages

Subjects follow [Conventional Commits](https://www.conventionalcommits.org/) — e.g.
`feat(harnesses): ...`, `fix(core): ...`, `docs: ...`, `ci: ...`, `build(release): ...`. An
advisory check flags non-conforming subjects; keep them imperative and scoped.

## Reporting issues

- **Security vulnerabilities:** do not open a public issue. Use the repository **Security** tab →
  **"Report a vulnerability"** (see `SECURITY.md`).
- **Bugs and harness gaps:** open a normal issue with reproduction steps and the affected
  harness/tool paths.

By contributing, you agree your contributions are licensed under the repository's
[MIT `LICENSE`](./LICENSE) and that you will uphold the [Code of Conduct](./CODE_OF_CONDUCT.md).
