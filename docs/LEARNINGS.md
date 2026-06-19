# Learnings - testing-kits

Distilled, thematic reference of the durable gotchas worth knowing before you repeat old
work (AGENTS.md points here). The per-PR / per-batch play-by-play — gate counts, branch
names, commit hashes — lives in git and PR history; this file keeps only lessons that recur.
Add a new lesson under the matching theme (date it inline if that helps).

## Proof model — what counts, and what the gates can't see

- **"Proven" means a verified swap-check, not string presence.** A harness is proven only when
  `prove(correct_oracle)` is False, every planted mutant makes it True, the corpus is non-empty,
  and a paired unittest plus a green self-test back it. The old keyword-based "77/77 proven"
  (matching "safe"/"bad"/"buggy" in source) was vacuous. Declaring module-level `TEETH` is the
  opt-in to `required` — there is no separate allowlist to drift.
- **Circularity is the failure the swap-check CANNOT catch.** A circular `prove` that compares
  `impl` to the oracle at runtime also satisfies oracle=False / mutant=True. Every `prove` must
  judge a FROZEN literal corpus. Adversarial proof it isn't circular: corrupt one corpus literal
  and watch `prove(oracle)` flip False→True, or monkeypatch the oracle to RAISE and confirm
  `prove` still answers. This matters doubly for AI harnesses (the answer-leak / stable-by-
  construction trap) — judge against frozen expected ids / verdict strings, never a model output.
- **"Self-test OK" can be hollow.** A harness with no argparse/main makes `--self-test` a silent
  no-op that exits 0. Trust the swap-check, not the exit code; every harness needs a real
  `Report`-based self-test.
- **Don't grep for "buggy twin" strings to claim a harness is GOLD** — it false-matches class
  libraries with no oracle/corpus (e.g. core/datetime). If teeth are absent, STOP and report;
  never fabricate them.

## The static checker gates (purity / circularity / corpus_size / dead_expr)

Stdlib-AST gates that catch structural failures the swap-check can't. Shared lessons:

- Resolve names through the module import map (an aliased `from time import monotonic` bypasses a
  bare leaf-name check); resolve `prove`/defs at MODULE scope, not a whole-tree walk; a truly
  dynamic call target is UNANALYZABLE, never silently "clean."
- **purity**: every `prove` must be clock / RNG / network / filesystem-free. It shipped broken once
  (`core/memory` defaulted a timestamp to `time.monotonic()` on the proof path).
- **corpus_size**: a naive `corpus_size == len(corpus)` gate is VACUOUS — every harness already
  writes exactly that. Anchor the count to the proof path instead: it must name a collection
  `prove` iterates or compares against (names merely passed as call arguments do NOT count). Alias
  resolution must accept pure name pass-throughs only (`a = b`; a CALL RESULT is not an alias of
  its args) and run to a fixpoint, not a fixed hop cap.
- **dead_expr** (advisory): flag a bare side-effect-free `ast.Expr` (Name/Attribute/Compare/BinOp/
  BoolOp/UnaryOp/Subscript, plus a pure-element Tuple for the assert-tuple / trailing-comma
  footgun); exclude Constant / Call / Await / Yield / walrus. Don't flag collection displays by
  outer type — they often hold side-effecting calls.

## Writing & upgrading harnesses

- **numeric mutant trap**: CPython 3.12+ `sum()` uses Neumaier compensation, so a `sum()`-based
  "naive sum" mutant is false-green on 3.12–3.14 — the buggy mutant must accumulate with an explicit
  `+=` loop. But don't borrow this framing where float drift isn't the real mechanism; verify the
  mutant is actually caught the way its comment claims (a payments mutant was really caught by a
  disabled Decimal guard, not by drift).
- **concurrency mutant trap**: real thread races are flaky — model the bad interleaving
  deterministically; `prove` must never spawn threads.
- Plant faithful mutants that model real bugs (RBAC fail-open, page-boundary `>=`/`>` off-by-one,
  int→float corruption at 2\*\*53+1, …), not trivial syntactic breaks. A mutant caught by only one
  load-bearing corpus case is fragile — add a second discriminating case when convenient.
- `kind` is `oracle_swap` for predicate harnesses, `auditor` for finding-producers, `statistical`
  for distribution oracles (seed the RNG; judge realized proportions against a frozen table).
- Modern security/ai harnesses don't need a separate `test_*_proof.py`: `TEETH` + a paired
  `TestTeeth` + an `assert_teeth` self-test supersedes it.

## Secret scanning — two scanners, both must be satisfied

- A fixture secret must be exempted in BOTH `tools/scan_staged.py` (a line containing the marker
  `allowlist secret`) AND gitleaks (`.gitleaks.toml` allowlist path/regex). The post-campaign
  hardened `scan_staged.py` also flags generic `NAME="..."` secret assignments the old one missed.
- `scan_staged.py` is deliberately divergent across repos (this repo WARNs on PII; the public repos
  BLOCK) — policy, not drift; read the docstring. testing-kits is the upstream of the family.

## EOL / CRLF and file_guard

- `.gitattributes` `* text=auto eol=lf` + `git add --renormalize` is the permanent fix for mixed-EOL
  churn; keep `autocrlf` false locally so the working tree stays LF.
- **`file_guard` hashes working-tree BYTES.** A protected `.py` left CRLF on Windows would mismatch
  CI's LF checkout and fail `make guard` — keep new/edited protected files LF (verify `CR=0` before
  committing). `.fileguard.json` itself comes out CRLF from `Path.write_text`, which is cosmetic (it
  isn't hashed and git normalizes it on commit). Adding a file to the protected set needs
  `make guard-update` in the SAME diff.
- A CRLF-contaminated working tree makes ruff emit a giant LF↔CRLF churn diff — restore exact blob
  bytes (`git checkout -- <dirs>`) before running it.

## Flaky tests

- The localhost mock-server self-tests (api / network / cache) flake under CPU contention (short
  socket timeouts). They pass in isolation, on retry, and on Linux CI — not a regression. `make
  proof` / `generate_report --check` / `make selftest` can all hit it; the real fix (a readiness-wait
  or retry on the mock servers) is still open.
- Run all-harness self-test/report commands SEQUENTIALLY — parallel `proof_audit` and
  `generate_report --check` collide on mock-server ports and create a false failure.

## CI and merge mechanics

- `required_conversation_resolution` means ANY unresolved bot review thread BLOCKS merge — resolve
  via the GraphQL `resolveReviewThread` mutation (REST can't). Codacy, SonarCloud, Conventional-
  commits, and CodeRabbit are NON-required (their red doesn't block); CodeRabbit skips small PRs,
  reviews large ones, and auto-resolves once a comment is addressed. `UNSTABLE` (advisory red) is
  mergeable; `BLOCKED` is not. Linear history only (squash/rebase); enforce_admins on, no bypass.
- Review bots post threads only AFTER `ready_for_review` (a draft gets no review). Don't mark a PR
  ready before completing the review-comment pass.
- The "Secret and dependency scan" (osv-scanner) fails on ANY vuln with no allowlist — including a
  newly-published advisory mid-PR that then blocks every PR and main → bump the dependency floor.
  Its occasional RPC "service unavailable" is a flake → `gh run rerun --failed`.
- Push and open PRs from `$HOME/_bk/tk`: that clone's `gh` token has push + workflow + PR scope; the
  MCP token does not.

## Status, inventory, and claims

- Filesystem discovery + `make proof` is the harness-count source of truth; committed STATUS files
  can be stale (`STATUS.md` / `STATUS.json` are generated and CI-artifact-only, not canonical).
- Describe the suite as "current proof baseline" or "checks passing," never "total correctness."
  Pharmacy docs stay limited to fixture-defined behavior — never clinical or medication-safety claims.
- Windows self-test/report needs explicit UTF-8 subprocess decoding; the console code page can crash
  report generation on Unicode harness output.

## Docs and tone

- Plain, direct tone — no hype ("forward-looking tripwire" / "red-locks main" → plain wording).
- Don't put PR-specific metadata in every permanent doc or dossier (doc-rot); keep closeout notes in
  the PR body or a dedicated section.
- Don't full-replace a large Markdown file from a truncated connector read (risks deleting unseen
  sections) — use line-window edits or a full local checkout (see `DOC_SIZE_POLICY.md`).
- Expand shell variables before pasting into logs (an unexpanded `$branch` once landed literally).

## Tooling and CI pins

- Dev tooling installs via `uv` (`[dependency-groups] dev`, `uv.lock`); CI keeps per-tool `pip`
  because the required path is pure-stdlib. mutmut is **Linux/WSL-only** (native Windows refuses), so
  the mandatory cross-platform gate is the stdlib swap-check; the mutmut lane is advisory
  (`continue-on-error`).
- Pin GitHub Actions to peeled tag SHAs verified with `git ls-remote` — annotated tags expose a
  `^{}` peeled entry, lightweight tags don't; use the peeled SHA. `zizmor` flags artipacked (checkout
  without `persist-credentials: false`) and template-injection (`${{ github.* }}` interpolated into a
  `run:` block) → fix both with env-var indirection.
