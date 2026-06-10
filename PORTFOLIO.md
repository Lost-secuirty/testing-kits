# Portfolio Reviewer Guide

This repository is a public, read-only-friendly showcase of testing and reliability patterns. It is intended to help a reviewer quickly understand what the project demonstrates without reading all 73 harnesses.

## What this repo demonstrates

- Python standard-library test-harness design.
- `unittest`-based validation without runtime dependencies.
- Built-in `--self-test` entry points for harnesses where applicable.
- Proof-oriented checks that verify harnesses can detect intended bad cases.
- Reliability, security, AI-evaluation, game/simulation, and pharmacy-domain testing patterns.
- Evidence discipline: current public state is generated into `STATUS.json` and cross-checked by `make proof`.

## What this repo does not claim

- It is not a production framework.
- It is not a packaged testing library.
- It does not claim senior software-engineering experience by itself.
- It does not claim clinical, legal, gambling, or regulated-production authority.
- Some harnesses are teaching/demo-scale patterns rather than complete enterprise tools.

## Fast reviewer path

Run the same commands shown in the README:

```bash
python --version
make test
make selftest
make proof
```

Expected current shape:

- 73 harnesses.
- 73 paired test files.
- 73/73 self-tests green.
- 73/73 proof checks green.

The generated `STATUS.json` is the compact machine-readable snapshot of the current public state.

## Best reviewer-facing harness groups

### AI and source-grounding evaluation

These harnesses are the strongest match for AI evaluator, prompt-QA, hallucination-review, and source-grounding roles.

- `harnesses/ai/rag_eval_test_harness.py`
- `harnesses/ai/prompt_injection_test_harness.py`
- `harnesses/ai/agent_memory_context_test_harness.py`

What they show:

- Prompt and retrieval-oriented QA thinking.
- Grounded-answer evaluation patterns.
- Prompt-injection and authority-boundary awareness.
- Agent memory/context failure-mode testing.

### Security and reliability patterns

These harnesses are useful for showing practical QA thinking around bad inputs, known-danger patterns, and regression checks.

- `harnesses/security/*`
- `harnesses/core/*regression*`
- `harnesses/core/*mutation*`
- `harnesses/core/*contract*`

What they show:

- Failure-mode labeling.
- Regression and contract testing concepts.
- Security-oriented test design.
- Bad-case proof discipline.

### Game and simulation testing

These harnesses connect the repository to game-QA, simulation-QA, and deterministic reliability-lab work.

- `harnesses/core/statistical_rng_oracle_test_harness.py`
- `harnesses/core/game_loop_simulation_test_harness.py`
- `harnesses/core/canvas_scene_state_test_harness.py`

What they show:

- RNG and statistical sanity checks.
- Game-loop invariant testing.
- Scene-state validation.
- Simulation reliability thinking.

### Domain-specific QA patterns

The pharmacy-domain harnesses are portfolio examples of domain-shaped QA, not clinical authority.

- `harnesses/pharmacy/*`

What they show:

- Domain-aware validation patterns.
- Data integrity and boundary checking.
- Regulated-context caution.

## How to describe this repo in applications

Safe wording:

> Public Python testing-harness portfolio with 73 standard-library harnesses, paired `unittest` suites, built-in self-tests, and proof checks across reliability, security, AI-evaluation, game/simulation, and pharmacy-domain testing patterns.

Use this repo to support claims like:

- AI-assisted QA workflow.
- Prompt-QA and source-grounding evaluation awareness.
- Manual QA and regression-test thinking.
- Failure-mode and bad-case testing discipline.
- Public documentation and evidence hygiene.

Do not use this repo to claim:

- Machine-learning engineering.
- Senior software engineering.
- Production security engineering.
- Clinical or regulated-production authority.
