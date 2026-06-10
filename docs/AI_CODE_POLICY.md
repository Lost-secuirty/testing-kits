# AI-Assisted Code Review Policy

AI-assisted code is useful, but it is not trusted by default.

Required for AI-assisted changes:

1. State what was AI-assisted.
2. State the risk area.
3. Run the repo verification commands.
4. Add or update tests when behavior changes.
5. Do not bypass security, CI, provenance, or review rules.

Reviewer question: what could silently fail?

For harness or test changes, use `docs/AI_AUTHORED_TEST_AUDIT.md`. AI-authored
or AI-assisted tests are untrusted until safe fixtures and planted-bad controls
demonstrate expected pass/fail behavior.
