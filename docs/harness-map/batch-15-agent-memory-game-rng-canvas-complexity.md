# Harness Map Batch 15

This file maps inventory entries #69-#73 in order: `ai/agent_memory_context`, `core/game_loop_simulation`, `core/statistical_rng_oracle`, `core/canvas_scene_state`, `core/complexity`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `ai/agent_memory_context` = `required`, `core/game_loop_simulation` = `required`, `core/statistical_rng_oracle` = `required`, `core/canvas_scene_state` = `required`, `core/complexity` = `pending`.

## 69. Agent Memory Context Test Harness

- Name: Agent Memory Context Test Harness
- Path: `harnesses/ai/agent_memory_context_test_harness.py`
- Category: `ai`
- Failure class: `tests/ai/test_agent_memory_context_test_harness.py`
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_retrieve`, `EXPECTED_RETRIEVED`, `MEMORY_CORPUS`.
- Planted-bad case: `cross_session_leak`, `evicts_pinned_item`, `budget_drops_required`
- Oracle / proof target: Current proof target: `oracle_retrieve`, `EXPECTED_RETRIEVED`, `MEMORY_CORPUS`, `SCENARIOS`.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Usage note: Use this as a memory/context fixture for retrieval scope, stale memory, cross-session leakage, and prompt-injection resilience.
- Current outside reference: OWASP LLM Top 10 covers prompt injection, sensitive information disclosure, and excessive agency risks relevant to memory/context handling. <https://owasp.org/www-project-top-10-for-large-language-model-applications/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/agent_memory_context_test_harness.py`; `python harnesses/ai/agent_memory_context_test_harness.py --self-test`; `python harnesses/ai/agent_memory_context_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_agent_memory_context_test_harness tests.ai.test_agent_memory_context_proof`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/game_loop_simulation`, `core/statistical_rng_oracle`, `core/canvas_scene_state`, `core/complexity`.

## 70. Game Loop Simulation Test Harness

- Name: Game Loop Simulation Test Harness
- Path: `harnesses/core/game_loop_simulation_test_harness.py`
- Category: `core`
- Failure class: `tests/core/test_game_loop_simulation_test_harness.py`
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_simulate`, `TICK_CORPUS`.
- Planted-bad case: `double_step_per_tick`, `skip_last_tick`, `input_starvation`
- Oracle / proof target: Current proof target: `oracle_simulate`, `TICK_CORPUS`.
- External testing pattern: game loop simulation fixture and regression testing.
- Usage note: Use this as a deterministic simulation fixture for tick ordering, fixed-step time, collision/event sequencing, and frame-independent behavior.
- Current outside reference: Gaffer on Games describes fixed timestep simulation as a stable game-loop testing pattern. <https://gafferongames.com/post/fix_your_timestep/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/game_loop_simulation_test_harness.py`; `python harnesses/core/game_loop_simulation_test_harness.py --self-test`; `python harnesses/core/game_loop_simulation_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_game_loop_simulation_test_harness tests.core.test_game_loop_simulation_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/statistical_rng_oracle`, `core/canvas_scene_state`, `core/complexity`.

## 71. Statistical RNG Oracle Test Harness

- Name: Statistical RNG Oracle Test Harness
- Path: `harnesses/core/statistical_rng_oracle_test_harness.py`
- Category: `core`
- Failure class: `tests/core/test_statistical_rng_oracle_test_harness.py`
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_sampler`.
- Planted-bad case: `biased_rng`, `cursor_drops_jackpot`, `truncated_range_rng`
- Oracle / proof target: Current proof target: `oracle_sampler`.
- External testing pattern: statistical rng oracle fixture and regression testing.
- Usage note: Use this as a statistical oracle fixture for seeded RNG distributions where exact value checks are too narrow.
- Current outside reference: NIST SP 800-22 describes statistical tests for random and pseudorandom number generators. <https://csrc.nist.gov/publications/detail/sp/800-22/rev-1a/final>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/statistical_rng_oracle_test_harness.py`; `python harnesses/core/statistical_rng_oracle_test_harness.py --self-test`; `python -m unittest tests.core.test_statistical_rng_oracle_test_harness tests.core.test_statistical_rng_oracle_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/game_loop_simulation`, `core/canvas_scene_state`, `core/complexity`.

## 72. Canvas Scene State Test Harness

- Name: Canvas Scene State Test Harness
- Path: `harnesses/core/canvas_scene_state_test_harness.py`
- Category: `core`
- Failure class: `tests/core/test_canvas_scene_state_test_harness.py`
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_analyze`, `SCENE_CORPUS`.
- Planted-bad case: `drops_duplicate_id_check`, `ignores_z_collisions`, `leaks_debug_nodes`
- Oracle / proof target: Current proof target: `oracle_analyze`, `SCENE_CORPUS`.
- External testing pattern: canvas scene state fixture and regression testing.
- Usage note: Use this as a scene-state fixture for draw-order, object transforms, hit state, and canvas-like rendering invariants.
- Current outside reference: MDN Canvas API documentation describes canvas rendering state and drawing primitives. <https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/canvas_scene_state_test_harness.py`; `python harnesses/core/canvas_scene_state_test_harness.py --self-test`; `python harnesses/core/canvas_scene_state_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_canvas_scene_state_test_harness tests.core.test_canvas_scene_state_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/game_loop_simulation`, `core/statistical_rng_oracle`, `core/complexity`.

## 73. Complexity / Bloat Test Harness

- Name: Complexity / Bloat Test Harness
- Path: `harnesses/core/complexity_test_harness.py`
- Category: `core`
- Failure class: Flags code bloat and maintainability regressions in AI-generated or human code. Computes cyclomatic complexity, cognitive complexity, function length, parameter count, and nesting depth with stdlib `ast`, then gates files or directories against configured thresholds. The self-test pins hand-computed metric values, proves a bad high-complexity fixture is flagged, and proves clean code passes.
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: complexity / bloat fixture and regression testing.
- Usage note: Use this as a maintainability fixture for size, branching, nesting, and bloat thresholds before accepting generated or refactored code.
- Current outside reference: Radon documents Python code metric analysis including cyclomatic complexity. <https://radon.readthedocs.io/en/latest/>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/complexity_test_harness.py`; `python harnesses/core/complexity_test_harness.py --self-test`; `python harnesses/core/complexity_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_complexity_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
- Related harnesses: `core/game_loop_simulation`, `core/statistical_rng_oracle`, `core/canvas_scene_state`.

## Batch 15 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-15-agent-memory-game-rng-canvas-complexity.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
