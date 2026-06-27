# CI status & live-state — read before you claim a PR is green or mergeable

Portable, tool-agnostic reference for any agent or human working a PR in a
Lost-secuirty repo. It exists because the expensive mistakes are not "the code
was wrong" — they are **"I reported a state that wasn't real."** A required check
that never posts, a bot commit that moved the head, a held workflow waiting on a
human: each looks like success or failure until you read the live state
correctly.

Source-of-truth order still applies: **live repo/PR/CI state wins** over this doc,
which wins over memory. When a label here and the live API disagree, trust the
API and fix the doc.

Every GitHub claim below was verified against primary docs (2026-06); see
**Sources**. The worked examples are real incidents from this org, by PR number.

---

## The live-state check — run it before any claim about a PR

Do not say "green", "merged", "blocked", or "done" until you have answered all of
these from **live state**, not memory:

1. **Which repo, branch, and PR number** am I actually on?
2. **`mergeStateStatus`** — read it (see table below). `UNKNOWN` means GitHub is
   still computing → re-poll, don't report.
3. **Required contexts vs what actually reported.** Diff the branch-protection
   `required_status_checks.contexts` against the checks present on the head SHA.
   A required context with **no producer** is the trap (below), not a pass.
4. **Failed checks — split required vs non-required.** A non-required red
   (Codacy, SonarCloud) does **not** block; a required red does. Don't bail a
   merge on a non-required failure, but still read the finding.
5. **Unresolved review threads.** With "require conversation resolution" on, an
   unresolved thread blocks merge and is **only visible via GraphQL** (not the
   REST PR object).
6. **Is the PR head still my commit, or did a bot move it?** An auto-fix/audit
   bot push (e.g. Codex's `audit:` commit) changes the head SHA — your old
   check results no longer describe the current head.
7. **Merge authority.** With `enforce_admins` on (all six repos), nobody bypasses
   a failing/absent required check — not even an admin. Some states need a human
   UI action (approve a held run), not a merge.

The commands are in the per-repo appendix. The discipline is the point: **if you
have not looked, say "unconfirmed," never "green."**

---

## `mergeStateStatus` — the 8 values (GraphQL `PullRequest.mergeStateStatus`)

GitHub's enum descriptions are terse; the *reason* for a BLOCKED state lives in
branch protection, not in the enum itself.

| Value | Meaning | What to do |
|---|---|---|
| `CLEAN` | Mergeable, required checks passing | OK to merge (if not draft) |
| `HAS_HOOKS` | Clean + a pre-receive hook will run | Treat as CLEAN |
| `BLOCKED` | A branch-protection requirement is unmet (required check missing/pending/failed, unresolved thread, or a held run) | Find *which* requirement — don't assume |
| `BEHIND` | Head is out of date with base (strict "require up to date" mode) | Update/rebase the branch; checks re-run |
| `UNSTABLE` | Mergeable, but a **non-required** check is red or pending | Mergeable; read the non-required finding |
| `DIRTY` | Merge commit cannot be cleanly created (e.g. conflict) | Resolve the conflict |
| `DRAFT` | (Deprecated — use `isDraft`) | Mark ready when appropriate |
| `UNKNOWN` | GitHub has not finished computing | **Re-poll**; never report from UNKNOWN |

A post-merge PR often reports `UNKNOWN` — that's expected, not an error.

---

## Check states — real API values vs the labels we say out loud

GitHub has **two** status systems. Required contexts match by **name** against
either.

- **Check runs** (GitHub Actions, CodeQL, Codacy app): a `status`
  (`queued` / `in_progress` / `completed`, plus Actions-only
  `waiting` / `requested` / `pending`) and, when completed, a `conclusion`
  (`success` / `failure` / `neutral` / `cancelled` / `skipped` / `timed_out` /
  `action_required` / `stale`).
- **Commit statuses** (legacy; **CodeRabbit posts one of these**): `error` /
  `failure` / `pending` / `success`. A commit status will **not** appear in a
  `check-runs` query — use the `status` endpoint or `statusCheckRollup`.

The words we use in conversation map onto those API values — and two of them are
**synthetic** (no API value), which is exactly where reporting goes wrong:

| We say | Real API value | Blocks a required gate? |
|---|---|---|
| passed | `conclusion: success` (or `skipped`/`neutral`) | no |
| running / pending | `status: in_progress` / `queued` / `pending` | yes, until it resolves |
| failed | `conclusion: failure` or commit-status `failure`/`error` | yes (if required) |
| cancelled | `conclusion: cancelled` | yes — **treat as failed** |
| skipped | `conclusion: skipped` | **no — counts as passing** (see below) |
| `action_required` | `conclusion: action_required` | yes — **a held gate, not a failure** |
| **absent** | *(synthetic — no check exists)* | **yes — the trap** |
| **unknown** | *(synthetic — can't determine)* | re-poll |

---

## The two "skipped" cases — opposite outcomes

This is the single most-confused point, so state it plainly:

- **A skipped job** (a job dropped by an `if:` or path filter, reported as
  `conclusion: skipped`) **counts as success** and does **not** block, even when
  it's a required context. Example: Codex's audit job self-skips on bot commits
  via its actor-guard → `skipped` → passing.
- **A workflow that never runs** (no producer, a path filter that excludes the
  change, or an external app that no-shows) creates **no check at all**. The
  required context sits at **"Expected — Waiting for status to be reported"** and
  **blocks the PR forever**, with nothing failing and nothing pending.

> **The absent-required trap.** Detect it by diffing required-contexts against
> what actually reported on the head SHA. A `BLOCKED` PR with every visible check
> green, no failures, and zero unresolved threads is almost always an absent
> required context. Fixes: make the producer reliably post, nudge it
> (`@coderabbitai review`), or stop requiring a context nothing produces. Never
> "fix" it by lowering the gate without understanding why it's absent.

---

## `action_required` — a held gate you approve, not a failure

When a workflow using the default `GITHUB_TOKEN` **creates or updates a PR** (e.g.
an audit job that pushes an auto-fix commit), the resulting runs are held in an
**approval-required** state — GitHub's recursion-prevention. They show as
`action_required` and the PR shows a banner; a human with write access clicks
**"Approve workflows to run"** to release them. This is an **intended gate**, not
a broken check. With `enforce_admins` on there is no bypass.

Do not confuse it with two other approval mechanisms: **fork-PR approval** (a
repo Actions setting for first-time/outside contributors — a banner, not a check
conclusion) and **deployment-environment required reviewers** ("Approve and
deploy"). Only the GITHUB_TOKEN-created-PR case applies to our auto-fix audits.

---

## Non-required failures, cancelled, and other edges

- **Non-required red ≠ blocked.** Codacy and SonarCloud quality gates are
  advisory here (`UNSTABLE`, not `BLOCKED`). Don't abandon a merge over them —
  but the finding can be real; read it. (A SonarCloud "new code" rating fail on a
  *testing-harness* repo is usually intentional planted-bad/fixture code; verify
  before "fixing" it, or it stops being a test.)
- **`cancelled` → treat as failed.** A concurrency-cancelled or manually
  cancelled run is not a pass; re-run it.
- **`stale`** is assigned by GitHub when a check is superseded; re-run.
- **Re-runs** keep the *original* trigger's privileges, cap at 50, and are
  available 30 days.
- **Merge queue** needs the `merge_group` trigger or required checks never report
  for the queued ref.
- **Auto-merge** will not fire while any required check is still "Expected."

---

## Worked examples (real, this org, 2026-06-20)

1. **Absent-required (CodeRabbit).** Demo-math `#40` (a Dependabot PR) was
   `BLOCKED` with `mergeable=MERGEABLE`, every other check green, **zero**
   unresolved threads. Cause: CodeRabbit (a *legacy commit-status* producer)
   skips draft and some bot PRs by default, so the required `CodeRabbit` context
   never posted → "Expected". An `@coderabbitai review` nudge made it post
   `CodeRabbit :: success`; the PR flipped `BLOCKED → CLEAN`. Durable fix: a
   `.coderabbit.yaml` with `auto_review.drafts: true` and no bot exclusions so
   the gate always resolves.

2. **`action_required` held bot-commit (Codex).** Codex-Speed-Test `#21`: the
   Drift Audit (`audit.yml`, `contents: write`) auto-pushed `audit: auto-fix +
   history`, moving the head off the human commit. The new head's CI/scan/audit
   runs were held `action_required` → `BLOCKED` until the operator approved them.
   The audit's `github.actor != 'github-actions[bot]'` guard then self-skips on
   the bot commit, so it does not loop. Working as designed.

3. **Environmental required-check failure (a fresh CVE).** DEP-TEST-KIT's
   `Audit + SBOM` runs OSV with "fail on any CVE." `pydantic-settings 2.14.1`
   picked up advisory **GHSA-4xgf-cpjx-pc3j** overnight, so the required check
   began failing on **every** PR regardless of its diff. The fix was a dependency
   bump (→ 2.14.2), not anything in the PRs it was blocking — diagnose the check,
   don't assume the diff.

---

## Per-repo appendix (required contexts + quirks)

`enforce_admins` is **on** in all six (no bypass). "strict" = require branch up
to date with base (causes `BEHIND` after a sibling merges). Read the live set with:

```sh
gh api repos/Lost-secuirty/<repo>/branches/main/protection --jq '.required_status_checks.contexts[]'
gh pr view <n> -R Lost-secuirty/<repo> --json mergeStateStatus,statusCheckRollup
```

| Repo | strict / conv-resolution | Required contexts (live set wins) | Quirk to know |
|---|---|---|---|
| Codex-Speed-Test | no / no | `audit`, `browser`, `checks`, `scan`, `smoke`, `CodeRabbit` | `audit.yml` auto-fixes & pushes → `action_required` held bot-commit each PR |
| Demo-math-slot-test-only | yes / yes | `analyze`, `audit`, `check`, `review`, `scan`, `smoke`, `CodeQL`, `CodeRabbit`, `Instruction and control audit`, `Pre-commit gates`, `Secret and dependency scan`, `Workflow and shell lint` | `check` runs `prettier --check .` over **all** files incl. config; `audit.yml` is read-only |
| Pharmacy-App | yes / yes | `analyze`, `lint`, `repo-health`, `review`, `scan`, `test (3.11–3.14)`, `CodeQL`, `CodeRabbit`, `Instruction and control audit`, `Pre-commit gates`, `Secret and dependency scan`, `Workflow and shell lint` | Tkinter/SQLite app; CodeRabbit posts a legacy commit-status |
| testing-kits | yes / yes | `analyze`, `review`, `scan`, `unittest (3.10–3.14)`, `CodeQL`, `CodeRabbit`, `Instruction and control audit`, `Package, install, and dependency audit`, `Pre-commit gates`, `Secret and dependency scan`, `Workflow and shell lint` | Canonical source repo for this doc; SonarCloud "new code" fails are usually fixture-by-design |
| Health-Prototype | yes / yes | `lint`, `test (3.10–3.13)`, `sensitive-scan`, `dependency-review`, `Analyze (actions)`, `Analyze (python)`, `CodeQL`, `CodeRabbit` | CodeQL is **default setup** — `Analyze (...)`/`CodeQL` post with **no** workflow file in `.github/workflows` |
| DEP-TEST-KIT | yes / no | `Audit + SBOM`, `Integration lane (Docker)`, `Lib lane + lint + dep-prune`, `Secret + workflow scan`, `Vacuous-green meta-gate`, `review`, `CodeRabbit` | `Audit + SBOM` fails on **any** CVE → environment-sensitive; `audit.yml` uploads history as an artifact (never pushes) |

`CodeRabbit` is being standardized as required across all six; it only becomes a
real gate once each repo's `.coderabbit.yaml` is merged and CodeRabbit is
confirmed posting (otherwise you re-create the absent-required trap).

---

## Sources (verified 2026-06)

- mergeStateStatus enum — GitHub GraphQL reference (`docs.github.com/en/graphql/reference/enums`).
- Check runs `status`/`conclusion` — `docs.github.com/en/rest/checks/runs`; commit statuses — `.../rest/commits/statuses`.
- Skipped/required & "Waiting for status to be reported" — `docs.github.com/.../troubleshooting-required-status-checks`.
- `GITHUB_TOKEN` not triggering runs + approval-required PR creation — `docs.github.com/en/actions/concepts/security/github_token`.
- Approving fork runs / deployment reviewers — `docs.github.com/en/actions/how-tos/manage-workflow-runs/{approve-runs-from-forks,reviewing-deployments}`.
- Protected branches / `enforce_admins` / rulesets — `docs.github.com/.../managing-protected-branches/about-protected-branches`.
- CodeRabbit auto-review (`drafts` default false; `@coderabbitai review`) — `docs.coderabbit.ai/configuration/auto-review`.
