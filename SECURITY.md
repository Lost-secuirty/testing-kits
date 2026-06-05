# Security & privacy policy

Two data tiers, and the rule separating them is the point of this policy.

## Data tiers — what goes where
- **GitHub (this repo): non-personal only.** Code, docs, notes, dev logs. Nothing that
  identifies a person or grants access.
- **Private Google Drive vault: everything personal or identity-linkable.** Feelings,
  life details, full name, address, phone, and any secret (API keys, tokens, passwords,
  private keys) — anything usable to impersonate, locate, extort, or prompt-inject me.

If in doubt, it goes in the Drive vault, not here.

## Sacred personal tier
`PERSONAL_JOURNAL*` and anything under `private/` belong to the Drive tier only. No
assistant reads, copies, moves, edits, or summarizes them without my explicit say-so,
each time, and they must never reach GitHub. The gate hard-blocks those paths.

## Automated gates (defense in depth, not a guarantee)
- `.gitignore` keeps secret/credential files and the personal tier out of staging.
- `tools/scan_staged.py` + `.githooks/pre-commit`: blocks commits that add a secret or a
  personal-tier path; warns (non-blocking) on PII. Activate per clone:
  `git config core.hooksPath .githooks`.
- `.github/workflows/scan.yml`: the same scan on every PR.
- `.claude/`: least-privilege tool settings + a guard that denies edits to secret files
  and the personal tier.

These reduce accidents; the human is the final gate.

## Incident runbook — a secret or personal data reached git
Assume anything that hit a remote is compromised the moment it landed. Order matters:
1. **Rotate / revoke the secret first** — treat it as burned, before touching history.
2. **Purge it from history** (`git filter-repo` / BFG) and force-push; coordinate.
3. If it reached a public surface, treat as fully disclosed.
4. Log what leaked, root cause, and fix so the gate can be improved.

For personal/Drive-tier material: remove + purge, move it to the Drive vault, log the
root cause.

## Reporting
Solo project; raise issues to me directly.
