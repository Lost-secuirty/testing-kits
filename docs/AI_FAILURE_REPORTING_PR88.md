# AI failure report - PR 88

Date: 2026-06-27.
PR: planned `pr-88/combinatorial-coverage-harness`.

## Tooling / agent issue

During PR 87, the assistant repeated a `create_branch` call after the branch already existed. This produced repeated `Reference already exists` errors before the assistant stopped the loop and continued safely.

## Recovery pattern

- Stop retrying a write action once the failure state is understood.
- Verify the actual branch/PR state before continuing.
- Record the deviation in the PR body.
- Keep the diff scoped to the approved PR area.

## Durable lesson

For branch-creation workflows, a `Reference already exists` result should usually be treated as a state signal, not as a transient failure. The next safe action is to inspect or write to the existing branch, not to retry branch creation.
