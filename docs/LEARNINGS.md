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
