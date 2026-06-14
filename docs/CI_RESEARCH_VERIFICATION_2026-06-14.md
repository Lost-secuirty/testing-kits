# CI Research — Verification + Gap Analysis (2026-06-14)

**Inputs:** two new AI-source research docs on CI, verified against primary sources and
mapped against the actual CI of `DEP-TEST-KIT` and `testing-kits`. Modeled on
`testing-kits/docs/RESEARCH_CLAIM_VERIFICATION_2026-06-13.md`.

**Sources**
- **Gemini (Drive)** — *"CI/CD Automation and Testing Strategies"* / "Advanced Continuous
  Integration Dynamics" (2026-06-14 07:27, id `1ZbhmHy…rm18`). Mutation testing, repo
  hygiene, pipeline bypasses, daemonless containers.
- **Gemini (Drive)** — *"Python CI Integration Testing Approaches"* (2026-06-14 00:16,
  id `15p9X2…6oUk`). Testcontainers; already fed the integration lane. Secondary.
- **ChatGPT (local)** — `Downloads/deep-research-report (1).md` — "Low-maintenance
  self-governing CI for small public Python repos."

**Scope:** DEP-TEST-KIT, testing-kits. No repo changes in this pass — recommendations only.

---

## Part A — Claims ledger (primary-source verified)

14 load-bearing claims, each checked by a web-research verifier **plus an independent
adversarial refuter** (primary sources only; 28 agents). Tally: **8 verified, 1 verified-
with-caveat, 2 misrepresented, 2 fabricated, 1 unsourced**. The **ChatGPT doc verified
100%.** The Gemini doc's *cited academic* figures are verbatim-accurate; its *uncited
hype numbers* are where every fabrication clusters.

| # | Claim (source) | Verdict | Primary source | Note |
|---|---|---|---|---|
| 1 | AI-gen test suite: 87% line / 52% branch / **38% mutation** / 62% undetected / 11% security (Gemini) | ❌ **Fabricated** | none found | Table exists only on a blog (techdebt.guru); the 87% is misappropriated from GitClear, which measures code *duplication*, not coverage. No academic source has these six numbers. |
| 2 | RMS↔PMS correlation = 0.015176 (Gemini) | ✅ Verified | [NSF 10538634](https://par.nsf.gov/servlets/purl/10538634) | Verbatim in AlBlwi/Ayad/Mili, AST 2024. |
| 3 | Diversity-aware mutation: 4.56× faults, 2.69× suite, Â₁₂=0.586, p=0.00338 (Gemini) | ✅ Verified | KAIST tech #334 (IEEE TSE) | Verbatim in Shin, Yoo & Bae. |
| 4 | MIST-RL: +28.5% mutation / −19.3% tests / +3.05% HumanEval+ (Gemini) | ✅ Verified | [arXiv 2603.01409](https://arxiv.org/abs/2603.01409) | Real paper (Zhu et al., 2026-03-02); figures verbatim. |
| 5 | pytest-gremlins 3.73×/13.82×, 117 vs 86 killed (Gemini) | ⚠️ Verified, **caveated** | dev.to / GitHub | Numbers accurate **but author's own** synthetic benchmark; **sequential mode is 0.84× — slower than mutmut**; tool <5 mo old, 0 dependents. Not "established." |
| 6 | "fest": Rust mutation tester ~25× faster than cosmic-ray (Gemini) | ❌ **Fabricated** | n/a | **No such tool exists.** Real Rust-backed Python mutation testers are `irradiate` and `pymute`; neither claims 25×. Tool name *and* stat invented. |
| 7 | mutmut timeout = (T_base × multiplier) + constant; mult 15.0, const 1.0 (Gemini) | 🟡 Misrepresented | [mutmut docs](https://mutmut.readthedocs.io/) | Precedence wrong — actual is **(T_base + constant) × multiplier**. Defaults correct. ".mutmut-cache = SQLite" unconfirmed; v3 uses a `mutants/` dir. |
| 8 | Rootless startup latency 0.30/0.35/0.42/0.45 s, +25–30% (Gemini) | 🟡 Unsourced | lucaberton.com (blog) | One self-published blog, no methodology, internally inconsistent. The only rigorous benchmark (markaicode) measures **seconds (1.2–2.4 s)**, not tenths. Direction plausible; numbers not citable. |
| 9 | Ryuk fails rootless → `TESTCONTAINERS_RYUK_DISABLED`; Podman net "podman"; ProviderPodman (Gemini) | ✅ Verified | [testcontainers-go docs](https://golang.testcontainers.org/system_requirements/using_podman/) | All confirmed (issue #537 is testcontainers-python/rootless-Docker — minor). |
| 10 | Docker pricing $9/$15/$24 + **1500+1500 min** (Gemini) | 🟡 Misrepresented | [docker.com/pricing/faq](https://www.docker.com/pricing/faq/) | Minutes are **tiered**, not flat: Pro 100+200, Team 500+500, **Business 1500+1500**. 1500+1500 is Business-only. Prices correct (annual; monthly $11/$16/$24). |
| 11 | TanStack/router compromise 2026-05-11: PR #7378, zblgg, pull_request_target, 84 versions/42 packages, OIDC theft (Gemini) | ✅ Verified | [TanStack postmortem](https://tanstack.com/blog/npm-supply-chain-compromise-postmortem) + GHSA-g7cv-rxg3-hmpx (CVE-2026-45321) + Wiz | Every element confirmed across 3 primaries. OIDC dumped via `/proc/<pid>/mem`. |
| 12 | `[skip ci]` tokens + pending-deadlock + "Merge OK" fix (Gemini) | ✅ Verified | [GitHub Docs](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/skipping-workflow-runs) | 5 tokens + deadlock confirmed; "Merge OK" is Pantsbuild's pattern, not GH-documented (minor). |
| 13 | gitlint conventional-commits (CT1, commit-msg + `--msg-filename`, `--commits` range) (Gemini) | ✅ Verified | [gitlint docs](https://jorisroovers.com/gitlint/latest/commit_hooks/) | All three parts verbatim. |
| 14 | ChatGPT GitHub-behavior bundle (dependency-review, full-SHA pin, deny-licenses deprecated, dismiss-stale-approvals, pull_request_target danger) | ✅ Verified | [dependency-review-action](https://github.com/actions/dependency-review-action) + GH docs | All five sub-claims confirmed. |

**Pattern (for the RAG archive):** consistent with your prior Gemini ledgers — Gemini
quotes the papers it *cites* accurately (claims 2–4 are verbatim-perfect, including a real
March-2026 arXiv ID), but invents *uncited* punch numbers (the 38% table), at least one
entire *tool* (`fest`), and subtly distorts mechanism details it paraphrases (the mutmut
formula precedence, Docker's tiered minutes presented as flat). The ChatGPT doc, being
citation-disciplined and conservative, verified 100%. **Net: trust the Gemini doc's
security/mechanism content and its cited academic stats; discard its uncited statistics
and the `fest` tool.**

---

## Part B — Gap matrix (recommendation × what your repos already do)

Legend: ✅ implemented · 🟡 partial · ❌ absent · ➖ N/A (correctly not needed)

| Recommendation (source) | DEP-TEST-KIT | testing-kits | Evidence / note |
|---|---|---|---|
| Dependency Review on PR (ChatGPT) | ❌ | ✅ | testing-kits `.github/workflows/dependency-review.yml`; DEP-TEST-KIT has none. Public repo → free. |
| Manager-native vuln audit (ChatGPT) | ✅ `uv audit` | ✅ pip-audit | DEP-TEST-KIT `ci.yml` supply-chain job; testing-kits CI job. |
| zizmor workflow static-analysis (both) | ✅ | ✅ | DEP-TEST-KIT `ci.yml` security-scan; testing-kits `controls.yml`. |
| Full SHA-pin + least-priv `permissions` (both) | ✅ | ✅ | Verified across all workflows; `permissions: contents: read` defaults. |
| SBOM on trusted branch (ChatGPT) | ✅ CycloneDX | ✅ SPDX | DEP-TEST-KIT supply-chain job; testing-kits `artifacts.yml`/`release.yml`. |
| Propose-only retros, **no bot-push to PR heads** (ChatGPT) | ✅ | 🟡 | DEP-TEST-KIT ADR-0004 is exactly this (comment + artifact, never push). |
| CodeQL (implied; testing-kits parity) | ❌ | ✅ | testing-kits `codeql.yml`; public repo → free. |
| OSV-Scanner cross-check (ChatGPT, non-blocking) | ➖ (uv audit is OSV-backed) | ✅ | testing-kits `controls.yml` runs osv-scanner. |
| Provenance/attestation for shipped artifacts (ChatGPT) | ➖ (ships nothing) | ✅ | testing-kits `release.yml` attests; DEP-TEST-KIT correctly defers (no artifacts). |
| `pull_request_target` used only for metadata (both) | ✅ none used | ✅ none used | **grep: zero matches anywhere** → structurally immune to the TanStack vector. |
| Trivy scheduled broad scan (ChatGPT, low priority) | ❌ | ❌ | Doc itself ranks this low for small repos; overlap not worth it. |
| **Mutation testing gated in CI** (Gemini) | 🟡 harness only | ❌ | DEP-TEST-KIT ships `mutmut` **harness** (`mutation_quality`) but no self-mutation CI gate. ⚠️ mutmut won't run natively on Windows (needs WSL); CI is Linux so fine. |
| gitlint Conventional-Commits gate (Gemini) | ❌ | ❌ | No commit-msg lint in either; commit style is informally conventional. |
| `[skip ci]` + "Merge OK" deadlock fix (Gemini) | ➖ | ➖ | Neither uses `[skip ci]`; the fix is only needed *if* you adopt skip tokens. |
| Toxiproxy chaos on integration lane (Gemini) | ❌ | ➖ (no integration lane) | Candidate enhancement for DEP-TEST-KIT's testcontainers harnesses. |
| PR-body template + validation (Gemini) | 🟡 | 🟡 | Both ship `pull_request_template.md`; DEP-TEST-KIT `audit_drift.py` checks the "Deviations" section. No CI grep-gate on the body. |
| Daemonless/Podman + Ryuk caveats documented (Gemini) | ❌ | ➖ | Worth a `docs/LEARNINGS.md` note (relevant on your Windows/Docker-Desktop box). |

**Headline:** your repos already implement nearly the entire **ChatGPT** doc — the only
real gaps there are *DEP-TEST-KIT adding `dependency-review` + CodeQL, which testing-kits
already has*. The **Gemini** doc is where the genuinely new (and more speculative)
material lives: mutation-in-CI, gitlint, chaos testing, daemonless caveats.

---

## Part C — Prioritized adoption (batch ≤6, signal-to-maintenance)

> Recommendations only — each is a separate, later approval. Ordered by value/upkeep.

1. **DEP-TEST-KIT: add `dependency-review` on PR** — closes a gap testing-kits already
   covers; PR-diff-scoped, public-repo-free, near-zero upkeep. *Blocking, low effort.*
2. **DEP-TEST-KIT: add CodeQL** (Python, schedule+PR) — free on public repos; parity with
   testing-kits. *Non-blocking, low effort.*
3. **Mutation gate, DEP-TEST-KIT first** — incremental (git-diff-scoped) `mutmut` on the
   harness code, **advisory first**, leveraging the dep you already ship. Caveat: local
   runs need WSL on Windows; CI Linux is fine. *Advisory→blocking later, medium effort.*
4. **gitlint Conventional-Commits** (shared across repos via the controls set) — cheap,
   unlocks future `semantic-release`. *Advisory, low effort.*
5. **Toxiproxy chaos harness** for the DEP-TEST-KIT integration lane — fits the batch
   model; proves connection-pool/retry resilience. *Advisory, medium effort.*
6. **Document daemonless/Ryuk/mutmut-WSL caveats** in `DEP-TEST-KIT/docs/LEARNINGS.md`.
   *Doc, trivial.*

**Do NOT re-add (already covered):** pull_request_target hardening (none used), zizmor,
SHA-pinning, least-priv permissions, SBOM, propose-only/no-push (ADR-0004), uv audit /
pip-audit, Renovate/Dependabot release-age cooldown, provenance (testing-kits only,
correctly deferred in DEP-TEST-KIT).

---

## Part D — Local tooling you can run on this machine

Python tools via `uv tool install` (uv 0.11.16 present); binaries via the verified winget
IDs. "Verifies" = which doc claim it lets you test first-hand.

| Tool | Install | Verifies / closes |
|---|---|---|
| mutmut | already a DEP-TEST-KIT dep (**WSL only** on Windows) | Your real mutation score vs the doc's "38%". |
| cosmic-ray | `uv tool install cosmic-ray` | mutmut-vs-cosmic-ray timeout claims. |
| pytest-gremlins | `uv tool install pytest-gremlins` | Real but immature (Jan 2026, parallel-only — **sequential is slower** than mutmut). Test the speedup yourself. |
| irradiate / pymute | cargo / PyPI | The *real* Rust-backed mutation testers. **`fest` from the doc does not exist** — use these if you want native speed. |
| act (nektos) | `winget install nektos.act` | Run `ci.yml`/`audit.yml` locally on Docker, no push. |
| OSV-Scanner | `winget install Google.OSVScanner` | Cross-check `uv audit`. |
| gitleaks | `winget install Gitleaks.Gitleaks` | Secret scan (testing-kits `controls.yml` uses it). |
| actionlint / shellcheck / shfmt | `winget install rhysd.actionlint` / `koalaman.shellcheck` / `mvdan.shfmt` | Workflow/shell lint (your `controls.yml`). |
| gitlint / pip-audit / zizmor / pre-commit | `uv tool install <name>` | Try the hygiene/audit gates before wiring into CI. |
| Podman Desktop | `winget install RedHat.Podman-Desktop` | The daemonless/Ryuk claims — optional (Docker already works). |

---

## Methods / caveats
- Part A verdicts come from a 14-claim adversarial verification workflow (verifier +
  independent refuter per claim, primary-source-only).
- Ground truth from this session's repo CI inventory (ci.yml/audit.yml/tools/ADRs in
  DEP-TEST-KIT; controls/codeql/scorecard/dependency-review/release in testing-kits) and
  `grep` confirmations (no `pull_request_target`; no gitlint/`[skip ci]`/mutation-gate).
- Live mutmut score **captured (WSL)**: with uv installed in Ubuntu WSL
  (`UV_PROJECT_ENVIRONMENT=/tmp/dtk-venv uv sync --extra lib`), the `mutation_quality`
  harness self-test runs real mutmut — **strong suite kills all mutants; weak suite leaves
  2 survivors** (the teeth gap reproduces). mutmut won't run on native Windows
  (boxed/mutmut#397), so the harness + its tests now skip there gracefully.
