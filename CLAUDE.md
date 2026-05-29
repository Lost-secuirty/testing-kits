# testing-kits — contributor guide

Pure-stdlib Python testing-harness collection. **Zero runtime dependencies.**

## Rule 0: zero runtime deps

Only the Python standard library at runtime. Dev tooling (`ruff`) lives in
`[project.optional-dependencies] dev` in `pyproject.toml`.

## Pattern

Every harness is a single self-contained Python file paired with a unittest
suite:

- Harness: `harnesses/<cat>/<name>_test_harness.py`
- Test:    `tests/<cat>/test_<name>_test_harness.py`

Each harness must include:

- Module docstring with one-line purpose + one-line `--self-test` command.
- `@dataclass` configs for tunables (no globals).
- `argparse` CLI with `--self-test`, `--help`, optional `--list-scenarios`,
  optional `--port`, optional `--verbose`.
- `_run_self_test() -> int` returning a process exit code.
- Optional `ThreadingHTTPServer` on a unique port (see table below) for
  networked harnesses. In-process harnesses skip this.
- `if __name__ == "__main__": sys.exit(main())`.

Start from `template/harness_template.py` — it has all the above as a
fill-in-the-blanks skeleton.

## Categories

| Dir | Scope |
|---|---|
| `harnesses/core/` | Reliability, correctness, data, perf, observability. |
| `harnesses/security/` | Auth, injection, supply chain, app-security. |
| `harnesses/ai/` | LLM eval, agents, prompt safety. |
| `harnesses/pharmacy/` | Pharmacy-domain harnesses. |

Tests mirror this structure under `tests/<cat>/`.

`experiments/` is a junk drawer for in-progress harnesses that are not yet
ready for the main set. Excluded from `make test` discovery.

## Commands

```bash
make test          # full suite
make test-fast     # pharmacy only (~3s)
make test-core     # core only
make test-security
make test-ai
make test-pharmacy
make selftest      # every harness --self-test
make report        # writes STATUS.md
make lint          # py_compile sanity + ruff if installed
make clean

python harnesses/<cat>/<name>_test_harness.py --self-test
python harnesses/<cat>/<name>_test_harness.py --list-scenarios
```

## Port table

In-use ports (default-bind in each harness). Pick from the reserved ranges
when adding a new networked harness.

| Port | Harness |
|---|---|
| 8080 | core/stress |
| 18900 | core/api |
| 18910 | core/scraper |
| 18920 | security/security |
| 18950 | core/concurrency |
| 18960 | core/fuzz |
| 18970 | core/property |
| 18980 | core/mutation |
| 18990 | core/regression_snapshot |
| 19010 | core/serialization |
| 19020 | core/config |
| 19030 | core/logging |
| 19050 | core/pipeline |
| 19060 | core/datetime |
| 19070 | core/idempotency |
| 19090 | core/numeric |
| 19110 | ai/llm_eval |
| 19160 | core/i18n |
| 19170 | core/pagination |
| 19180 | core/a11y |
| 19190 | ai/agentic |
| 19200 | security/supplychain |
| 19210 | security/upload |
| 19240 | pharmacy/clinical_calc |
| 19250 | pharmacy/lockout |
| 19270 | pharmacy/auditlog_cap |
| 19280 | pharmacy/expiry_window |
| 19290 | pharmacy/partial_fill |
| 19300 | core/payments (reserved; oracle runs in-process) |
| 19310 | core/graphql (reserved; oracle runs in-process) |
| 19320 | core/search_relevance (reserved; oracle runs in-process) |
| 19330 | core/circuitbreaker |
| 19400 | security/jwt |
| 19410 | security/pii_redaction |

Note: `core/tracing`, `core/queue`, and `ai/rag_eval` are pure in-process
oracles and bind no port.

**Reserved ranges for future harnesses:**

- `19040-19049` — open
- `19100, 19120-19150` — open
- `19220-19230` — open
- `19300-19399` — reserved for core (19300/19310/19320/19330 now claimed)
- `19400-19499` — reserved for security (19400/19410 now claimed)
- `19500-19599` — reserved for ai
- `19600-19699` — reserved for pharmacy

## Adding a harness

1. `cp template/harness_template.py harnesses/<cat>/<name>_test_harness.py`
2. If networked: pick an unused port, add to the table above.
3. Implement the harness; preserve the `--self-test` exit-code contract.
4. Write the paired test in `tests/<cat>/test_<name>_test_harness.py`.
5. `python harnesses/<cat>/<name>_test_harness.py --self-test`
6. `make test`
7. `make report` to regenerate `STATUS.md`.
8. Commit. One harness per commit keeps history bisectable.

## Test discovery

`make test` runs `python -m unittest discover -s tests -t . -p "test_*.py"`.
Tests import their harness via `from harnesses.<cat>.<name>_test_harness
import ...`. The repo-root `-t .` flag lets the `harnesses` package resolve
during discovery.
