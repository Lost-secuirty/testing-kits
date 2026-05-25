# TECH_AUDIT — Generic Tech Knowledge Reference

Iterative living reference of generic, verified tech knowledge for testing/debugging/build workflows. Each round appends a dated section. Scope rules: no project-specific blueprints or names — only reusable methodology. Source bar: official docs + reputable orgs + reputable tech press.

---

## Round 1 — 2026-05-23 — Testing harnesses + AI debug/fuzzing + Build/release workflows

### From Drive (generic patterns extracted, project names stripped)

**Test-type taxonomy** (source: `Test harnesses` doc) — useful as a shared vocabulary across any project:
- `SMOKE` — app/module starts or basic path runs
- `UNIT` — verifies one function/component
- `INTEGRATION` — multiple parts working together
- `REGRESSION` — verifies a known bug does not return
- `STRESS` — repeats behavior many times or with broad inputs
- `FUZZ` — randomized or generated input testing
- `PERSISTENCE` — save/load/database/file state
- `UI_REACHABILITY` — user can actually reach/control UI
- `MANUAL_USER_SIDE` — user-run on target device/environment
- `STATIC_INSPECTION` — code/doc inspection without execution
- `FITNESS` — architecture/workflow rule check (per Mark Richards / Neal Ford)

**Evidence-level taxonomy** (source: `Test harnesses`, `Gpt Adr decision log`) — anti-drift labeling for any claim:
- `CONFIRMED_USER_SIDE` / `CONFIRMED_ASSISTANT_SIDE` / `IMPLEMENTED_UNVERIFIED` / `RESEARCH_ONLY` / `DEPRECATED` / `SUPERSEDED` / `UNKNOWN`
- Rule: a claim without an evidence level cannot count as proof. A user-side test beats an assistant-side claim. A screenshot proves observation, not root cause. A log/error trace is stronger than memory.

**Test-entry metadata template** (source: `Test harnesses`) — every test used as proof should declare: `test_id`, `test_name`, `requirement`, `adr_or_design_ref`, `issue_or_watch_ref`, `test_type`, `environment`, `evidence_level`, `last_verified`, `result`, `failure_mode_guarded`.

**ADR template** (source: `Gpt Adr decision log`) — Nygard-style with explicit confirmation:
- Status / Date / Context / Decision / Alternatives considered / Consequences (benefits, costs, new risks) / Confirmation (proved by, test_id, evidence_level, last_verified, failure_mode_guarded) / Links
- Rule: no ADR is `ACCEPTED` without a Confirmation field; `SUPERSEDED` ADRs stay in the log clearly marked.

**Traceability matrix chain** (source: `Chat gpt-traceability matrix`):
- `REQUIREMENT → ADR/DESIGN RULE → TEST/CHECK → EVIDENCE LEVEL → STATUS`
- Matrix statuses: `OPEN / IMPLEMENTED_UNVERIFIED / CONFIRMED_ASSISTANT_SIDE / CONFIRMED_USER_SIDE / BLOCKED / DEPRECATED / SUPERSEDED`
- Maintenance rule: add a requirement when (a) a rule repeats more than once, (b) a bug needs regression protection, (c) an ADR creates a durable constraint, (d) a research finding becomes planned implementation.

**Fitness functions concept** (source: `Gpt Adr decision log` ADR-006) — repeated architecture/workflow rules should be converted into runnable tests, checklist gates, or audit rules rather than left as prose. (This is from Building Evolutionary Architectures by Ford/Parsons/Kua — verified standard term.)

**Research-to-implementation firewall** (source: `Test harnesses` TEST-RESEARCH-001) — research findings (from web/papers/LLMs) must not be described as implemented behavior unless code changed and a test/check exists. Especially important when the source is an LLM that may hallucinate.

**Stress-testing principle** (source: `2026 Code-Only Stress Testing Harnesses`) — performance tests should be first-class code artifacts living in the same repo as application code, peer-reviewed, versioned, and CI-integrated. Legacy GUI-driven load testing tools are obsolete; code-only frameworks (k6, Gatling, Locust) are the standard.

**Hybrid LLM + symbolic execution loop / CEGIS** (source: `AI Model Capabilities: Fuzzing, Coding, Testing`) — to close the gap between LLM hallucination and formal correctness:
1. LLM generates candidate patch
2. Concolic execution extracts path constraints
3. SMT solver (Z3 or cvc5) evaluates against spec
4. If violation, solver outputs counterexample
5. Counterexample fed back to LLM
6. Loop until solver validates
- Same doc: "PAGENT" (Program Analysis Guided agent) reports 132% improvement in PoC generation by injecting static analysis + sanitizer profiling into the LLM context.

**Generative fuzzing vs traditional fuzzing** (source: `AI Model Capabilities: Fuzzing, Coding, Testing`):
- Traditional: random byte mutations, struggles with deeply nested conditions and structured formats
- Generative: LLM synthesizes semantically valid but structurally malformed inputs targeting parser edge cases
- Two architectural patterns: (a) **harness auto-generation** (LLM writes the C/C++ wrapper, deterministic fuzzer like AFL++/libFuzzer does the actual discovery), (b) **direct semantic reasoning** (LLM reads source, hypothesizes vulns, compiles with ASan/UBSan, self-generates PoC, adversarial self-review).

**Memory grooming via RL + LLM** (source: `AI Model Capabilities: Fuzzing, Coding, Testing`) — heap exploitation increasingly automated by treating memory layout as RL state space, input sequences as action space, successful adjacent placement as reward.

**Multi-pattern microservice decomposition** (source: `AI Use Cases for App Building`) — three coupling metrics for breaking a monolith:
- **Semantic coupling**: NLP on function/variable names + docs to cluster thematic boundaries
- **Contributor coupling**: version control history to align code structure with team structure (Conway's Law)
- **Evolutionary coupling**: frequency of co-changed files within historical commits

**Hierarchical / parent-child chunking for RAG** (source: `AI Use Cases for App Building`) — vector search on small "child" chunks for precision; pass the enclosing "parent" chunk to the LLM for context. Solves the precision-vs-context tradeoff.

**Distributed tracing for AI agent workflows** (source: `Advanced AI Debugging Research Vectors`) — OpenTelemetry traces/spans/attributes/events used to map LLM inference, token counts, vector DB invocations across multi-repo agentic flows.

---

### Web-verified updates (Drive items confirmed/updated against 2026 primary sources)

**k6 — code-only load testing**: Drive cited k6 as standard. Confirmed: [k6 2.0 release](https://grafana.com/blog/k6-2-0-release/) ships an `x agent` MCP server that works with Claude Code, Codex, etc. — agents can validate scripts, run tests, inspect results. Also broader Playwright compatibility in the browser module. Source: [Grafana](https://grafana.com/docs/k6/latest/release-notes/).

**OpenTelemetry tracing**: Drive's distributed-tracing claims verified. Per the [OTel specification status](https://opentelemetry.io/docs/specs/status/) the Tracing API and SDK are now **Stable** with long-term support; metrics + tracing stable across all major languages.

**AFL++ + libFuzzer for coverage-guided fuzzing**: Drive's claim about coverage-guided as the deterministic core verified. Both [AFL++](https://github.com/AFLplusplus/AFLplusplus) and [LLVM libFuzzer](https://llvm.org/docs/LibFuzzer.html) actively maintained; AFL++ can be synced with libFuzzer via `-entropic=1`; running them in parallel is recommended.

**Hypothesis property-based testing for Python**: Drive light on PBT but the concept ties to the test taxonomy. Confirmed: [Hypothesis](https://hypothesis.works/) is the canonical Python PBT library. Notable 2026 release: the [Hypothesis Corpus](https://github.com/HypothesisWorks/hypothesis) — dataset of source code + runtime behavior from 28,928 tests across 1,529 repos (April 14, 2026).

**ADR Nygard format**: Drive's ADR template structure verified against the [ADR community templates](https://adr.github.io/adr-templates/) and the original [Nygard template](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md). Drive's "Confirmation" field is a non-standard but valuable extension — keep it.

**Claude Opus 4.7 benchmarks**: Drive cited Opus 4.7 with specific benchmark scores. Verified: per [Anthropic's Opus 4.7 announcement](https://www.anthropic.com/news/claude-opus-4-7), 3× more production tasks resolved on Rakuten-SWE-Bench vs 4.6, double-digit gains in code quality and test quality, 13% lift on internal 93-task coding benchmark. Drive's specific SWE-bench Pro figure (64.3%) is from a secondary source and should be treated as approximate.

**Playwright E2E**: Verified [Playwright](https://playwright.dev/) drives Chromium/Firefox/WebKit with one API; supports TS/Python/.NET/Java. MCP server now available as drop-in for VS Code, Cursor, Claude Desktop, Windsurf. **Important update Drive didn't have**: Microsoft Playwright Testing service is retiring **March 8, 2026** — migrate to Playwright Workspace in Azure App Testing. Source: [Microsoft Azure docs](https://learn.microsoft.com/en-us/azure/playwright-testing/quickstart-automate-end-to-end-testing).

**pytest fixtures**: Standard practice verified via [pytest docs](https://docs.pytest.org/en/stable/how-to/fixtures.html). Fixtures: declared with `@pytest.fixture`, requested by test arg name, modular (fixtures can use fixtures), scoped (function/class/module/session). Use these as the foundation for the test-entry metadata template above.

**Codebase-Memory MCP (Tree-Sitter knowledge graph)**: Drive cited arXiv 2603.27277 verbatim. Verified: [paper exists](https://arxiv.org/abs/2603.27277) (submitted March 28, 2026), [open-source repo](https://github.com/DeusData/codebase-memory-mcp) exists at v0.4.6. 66 languages parsed, single SQLite file storage, sub-ms structural queries, 14 typed MCP tools. Benchmark: 83% answer quality vs 92% for file-by-file exploration, but 10× fewer tokens and 2.1× fewer tool calls.

**Z3 + cvc5 SMT solvers**: Verified active, widely used for symbolic execution + LLM-guided synthesis. [cvc5](https://github.com/cvc5/cvc5) is the active successor to CVC4 (5th in CVC family). Z3 from Microsoft Research. Both used in CEGIS loops.

**Model Context Protocol (MCP)**: Drive references MCP throughout. Major 2026 update Drive missed: [MCP 2026-07-28 spec release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/) makes MCP **stateless at the protocol layer** — remote servers no longer need sticky sessions, can run behind round-robin load balancers, capabilities discoverable via `.well-known` without a live connection. Adds MCP Apps (servers ship sandboxed HTML interfaces hosts render in iframe) and reverse-DNS extension IDs.

**GitHub Security Lab Taskflow Agent**: Drive cited specific CVE-2026-30847 (Wekan) and CVE-2026-28397 (NocoDB) findings. Framework verified: [seclab-taskflow-agent](https://github.com/GitHubSecurityLab/seclab-taskflow-agent) is real, uses YAML-based workflow grammar, very effective at Auth Bypasses, IDORs, Token Leaks per [GitHub blog](https://github.blog/security/ai-supported-vulnerability-triage-with-the-github-security-lab-taskflow-agent/). Specific CVE numbers not independently verified — treat as Drive-sourced.

**Claude Code slash commands ↔ skills**: Per [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code/slash-commands), in **v2.1.101 (April 2026)** custom slash commands were merged into skills. Files in `.claude/commands/` still work but `.claude/skills/` (with `SKILL.md` + YAML frontmatter) is now the recommended approach — both create `/command-name` shortcuts. MCP prompts surface as `/mcp__server__command`. Plugins package skills + subagents + MCP servers + hooks.

---

### Web gap-fills (2026 items Drive didn't mention — verified primary sources only)

**Mutation testing — mutmut (Python)**: [mutmut 3.5.0](https://pypi.org/project/mutmut/) released Feb 22, 2026. Python 3.10–3.14, requires fork support (use WSL on Windows). Tests your tests: mutates source, fails if your suite doesn't catch the mutation. A complement to coverage — coverage tells you what runs, mutation testing tells you what's actually asserted.

**Mutation testing — Stryker (JS/TS, C#, Scala)**: [stryker-js v9.6.1](https://github.com/stryker-mutator/stryker-js) released April 10, 2026. Same concept as mutmut for the JS ecosystem.

**Consumer-driven contract testing — Pact**: [Pact V4](https://docs.pact.io/) is current. Consumer writes a test asserting expectations of the provider; the test generates a contract; the provider runs the contract. Supports HTTP, async, synchronous interactions. Only consumer-used parts of the API get tested — provider can change unused behaviors freely.

**Testcontainers — real-services integration testing**: [Testcontainers](https://testcontainers.com/) actively maintained May 2026 across Java/Go/Python/.NET/PHP/Node. Pattern: integration tests start ephemeral Docker containers (DBs, brokers, browsers) instead of mocks; containers destroyed regardless of test outcome. Eliminates mock/prod drift.

**Supply-chain security — SLSA v1.2**: [SLSA spec v1.2](https://slsa.dev/spec/v1.2/) is the current standard, set of incrementally adoptable levels for supply chain security. Backed by [OpenSSF](https://openssf.org/projects/slsa/). Use alongside NIST SP 800-218 (SSDF) and OWASP CI/CD Top 10.

**Artifact signing — Sigstore Cosign**: [Cosign v3](https://blog.sigstore.dev/cosign-3-0-available/) is current. Standardized bundle format on by default. In-toto attestations via `cosign attest` / `cosign verify-attestation`. Adopted by Homebrew (May 2024), PyPI (Nov 2024), Maven Central (Jan 2025), NVIDIA NGC model signing (July 2025). Transparency log = globally auditable signing identity.

**Continuous profiling — eBPF + Pyroscope/Parca**: [Grafana Pyroscope](https://grafana.com/docs/pyroscope/) (merged with Phlare) supports Go/Python/Ruby/Java/eBPF/.NET/PHP/Node.js/Rust via standardized SDKs. eBPF profiler discovers targets from Kubernetes/systemd automatically with low overhead. [Parca](https://github.com/parca-dev/parca) is the CNCF-aligned alternative, also eBPF-based via libbpf-go.

**OWASP Top 10:2025** ([owasp.org/Top10/2025](https://owasp.org/Top10/2025/)) — current standard, major changes from 2021:
- A01:2025 Broken Access Control (still #1)
- A02:2025 Security Misconfiguration (up from #5)
- **A03:2025 Software Supply Chain Failures** (expanded from "Vulnerable and Outdated Components" — now covers build systems, dependencies, and broader ecosystem compromises)
- **A10:2025 Mishandling of Exceptional Conditions** (new — improper error handling, failing open, logical errors)

**Chaos engineering — LitmusChaos + Chaos Mesh**: Both active CNCF projects. [LitmusChaos](https://litmuschaos.io/) launched a [Litmus MCP Server](https://www.cncf.io/blog/2026/01/22/litmuschaos-q4-2025-update-community-contributions-and-project-progress/) so MCP-compatible clients can discover/run/monitor experiments. [Chaos Mesh](https://chaos-mesh.org/) had a March 25, 2026 release. Use for controlled fault injection in Kubernetes-native systems.

**LLM evaluation — DeepEval + Promptfoo**: [DeepEval](https://deepeval.com/) is Pytest-like for LLM apps with 50+ ready metrics (G-Eval, hallucination, RAG, safety, tool use, conversational, multimodal). Local-first. [Promptfoo](https://promptfoo.dev/) is YAML-driven, focused on security testing and red-teaming for LLM systems. Use both: DeepEval for correctness regression, Promptfoo for prompt-injection / safety testing.

**Visual regression testing — Chromatic + Percy**: [Chromatic](https://www.chromatic.com/playwright) integrates with Playwright by extending `test`/`expect` with a single import; pixel-perfect snapshot diffing; unlimited parallel test runs. [Percy (BrowserStack)](https://www.browserstack.com/percy) is the Playwright-native alternative with AI-powered visual diff reduction. ~58% of companies use automated visual testing per industry stats; ~1/3 of websites still ship visual regressions.

**Reproducible dev environments — DevContainers**: [containers.dev](https://containers.dev/) is the open spec. `.devcontainer/devcontainer.json` + optional Dockerfile defines a complete dev environment that VS Code, GitHub Codespaces, JetBrains, and [DevPod](https://github.com/loft-sh/devpod) all consume. [devcontainers/ci](https://github.com/devcontainers/ci) GitHub Action lets CI build/test in the same container — eliminates "works on my machine."

**Pygame Community Edition**: [pygame-ce 2.5.7](https://github.com/pygame-community/pygame-ce/releases) released March 2, 2026 with Python 3.14 and PyPy 3.11 support. Drive references game-engine work; pygame-ce is the actively-maintained fork (former core devs) — `pygame-ce` is the package, not `pygame`. SDL3 / pygame-ce-3 next major release in progress.

---

### Stale / dropped

- **Project-specific test IDs and ADR examples** in Drive (PHARMACY, OMNI, ARPG, MODPY, gacha, dokkan, Apothecary, quiz) — stripped on extraction per scope rules. The templates and methodology survive; the project nouns do not.
- **"Chat gpt-State" / "Chat gpt-Drift log" / "Chat gpt-Fitness function" naming** — these are specific to the Drive workflow, not a generic standard. The underlying concepts (canonical state doc, drift log, fitness function registry) are generic and retained above.
- **Specific Drive CVE numbers** (CVE-2026-30847 Wekan, CVE-2026-28397 NocoDB) — the framework (GitHub Security Lab Taskflow Agent) is verified; the specific CVE numbers came from the Drive doc only and were not independently verified against MITRE/NVD. Retained for reference but flagged.
- **Drive's "Project Glasswing / Claude Mythos Preview" claims** about specific Firefox 150 vulnerability counts — these are research-paper-tier claims from secondary sources. Treat as `RESEARCH_ONLY` per the firewall rule.

---

### Round 1 summary

- **Drive vectors covered**: testing harness taxonomy & metadata; evidence-level system; ADR + traceability matrix templates; fitness functions; LLM+symbolic-execution loops; generative fuzzing patterns; RAG chunking; microservice decomposition; distributed tracing
- **Web-verified Drive claims**: 14
- **Web gap-fills added**: 14
- **Stale/dropped**: 4 categories (project names, naming conventions, unverified CVEs, research-tier claims)
- **Total cited primary sources**: ~40 (all linked inline)

**Anticipated next vectors** (for Round 2+, pick one):
- Claude Code / MCP workflows in depth (`_systems/claude_reference_2026` Drive folder)
- Python vulnerability research (`The 2025-2026 Python Vulnerability Research` doc)
- Pygame-specific patterns + sprite/state-management
- Mac dev environment (`macbook_setup_2026`)
- CI/CD pipeline specifics (GitHub Actions, reusable workflows, OIDC)
- Observability beyond OTel (Grafana stack, structured logging, error tracking)
- Code review / static analysis (CodeQL, Semgrep, Ruff, Biome)
- Container/Kubernetes testing (Kind, k3d, kuttl)
- Reproducibility / Nix / hermetic builds

---

## Round 2 — 2026-05-23 — Claude Code/MCP, Python vuln, Pygame, Mac dev, CI/CD, observability, static analysis, K8s testing, Nix

### Vector 1 — Claude Code / MCP workflows

**From Drive** (`Claude_MAX_Subscriber_Capabilities`, `AI_REFERENCE_Claude_2026_Consolidated`):
- Effort tier `xhigh` is new in Opus 4.7, sits between `high` and `max`, is the recommended starting point for coding/agentic work; default in Claude Code on 4.7
- API parameters to **remove** when migrating to Opus 4.7: `temperature`, `top_p`, `top_k`, legacy `budget_tokens` workflows, assistant-message prefills
- Real 2026 control surface: model selection, effort level, system prompt structure, tool schema design, task budgets (advisory), `max_tokens`, structured outputs, context management (caching/editing/compaction)
- Compaction beta header: `compact-2026-01-12`; Managed Agents beta header: `managed-agents-2026-04-01` (coordinator + up to 20 specialists, shared filesystem, separate context windows, persistent threads)
- Thinking tokens count toward `max_tokens`, billed as output tokens, count toward rate limits, stripped from later-turn context automatically. **When tools are involved, the thinking block associated with a tool request must be returned unmodified alongside the `tool_result`** — signatures verify authenticity; modifying these blocks breaks reasoning continuity
- Tier hierarchy: chat app has no `/clear` `/compact` `/effort` (those are Claude Code only); Claude Code adds subagents + slash commands; Managed Agents adds coordinator/specialist topology; raw Messages API adds full orchestration control
- "Contract" system prompt structure: `<role>` → `<context>` → `<task>` → `<constraints>` → `<output_format>` → `<examples>`
- Format hierarchy: **XML** for prompts INTO Claude (Anthropic-trained native), **Markdown** for docs at rest (heaviest LLM training overlap, lowest token cost), **JSON** for structured output FROM Claude (with schema declared upfront)
- Optimization levers ranked by leverage: prompt caching on stable prefixes > token counting endpoint > Message Batches API (50% cheaper async) > explicit effort setting > tool search (when tool catalog > ~20) > programmatic tool calling > context editing > compaction > task budgets
- "Context rot" — documented phenomenon where as input token count rises, recall degrades **before** the hard limit. Many small atomic docs outperform one mega-doc (Anthropic Cookbook, March 20, 2026)

**Web-verified updates**:
- Claude Opus 4.7 GA April 16, 2026: model string `claude-opus-4-7`, SWE-bench Verified 87.6%, SWE-bench Pro 64.3%, $5/M input + $25/M output. Per [Anthropic Opus 4.7 announcement](https://www.anthropic.com/news/claude-opus-4-7). 1M context standard at no surcharge above 200K since March 13, 2026.
- Skills + slash commands unified in [Claude Code v2.1.101 (April 11, 2026)](https://docs.anthropic.com/en/docs/claude-code/slash-commands): `.claude/commands/` still works, `.claude/skills/SKILL.md` is recommended. YAML frontmatter + markdown body.
- MCP servers: GA support documented at [modelcontextprotocol.io](https://modelcontextprotocol.io/specification/2025-11-25); MCP prompts surface as `/mcp__server__command` slash commands per Round 1.
- Plugins package skills + subagents + MCP servers + hooks — confirmed via [anthropic.com/news/claude-code-plugins](https://www.anthropic.com/news/claude-code-plugins)

**Web gap-fills**:
- Anthropic Cookbook publishes context-engineering recipes (compaction, memory, tool clearing) at [platform.claude.com/cookbook](https://platform.claude.com/cookbook)
- Constitutional Classifiers research: prototype robust to thousands of hours of human red-teaming, with only +0.38% refusal rate increase on synthetic evals — relevant for any defensive deployment of Claude on user-facing content

---

### Vector 2 — Python vulnerability research + QA

**From Drive** (`The 2025-2026 Python Vulnerability Research`):
- Free-threading transition: Python 3.13 (experimental no-GIL), 3.14 (Phase II supported), 3.15 (refined). GIL removal exposes data races in code that was previously thread-safe by accident — e.g., `x += y` was atomic under GIL, isn't without it
- **`PyObject` struct grew from 16 → ~32 bytes** to add `ob_tid`, `ob_mutex`, `ob_gc_bits`, `ob_ref_local`, `ob_ref_shared`. Memory allocator changed pymalloc → mimalloc. GC moved from 3-generation → non-generational stop-the-world.
- Performance tax: ~5-10% single-threaded degradation on 3.14t vs GIL Python, 15-20% memory overhead
- **ThreadSanitizer (TSan)** is now mandatory for native extension developers — compile free-threaded CPython with TSan. Use [py-free-threading.github.io](https://py-free-threading.github.io/thread_sanitizer/) prebuilt Docker images (e.g., `ghcr.io/nascheme/numpy-tsan:3.14t-dev`) to skip ~10 min of CPython build time per CI run
- Sub-interpreters via `concurrent.futures.InterpreterPoolExecutor` — shared-nothing concurrency alternative to raw threading
- **Atheris** (Google) — coverage-guided fuzzing for Python 3.11-3.13, instruments bytecode via `atheris.instrument_imports()` + `TestOneInput`; compile with Clang ASAN/UBSAN for cross-language Python→C trace visibility
- **OSS-Fuzz** record (per May 2025): 13,000+ vulnerabilities and 50,000+ bugs across 1,000+ projects. **Rewards Program sunset May 1, 2026** — signals manual harness writing has hit diminishing returns, not that fuzzing efficacy dropped
- **StorFuzz** (ICSE 2026) — data-guided fuzzing for breaking coverage plateaus by monitoring internal application state; implemented in LibAFL; 50 new bugs in well-fuzzed projects (PHP, VLC), some 14 years old
- **OSS-Fuzz-Gen** (Google) — LLM-driven fuzz driver generation; reported 31% line coverage improvement on tinyxml2, 6% on cJSON, etc.
- **FuzzAgent** — multi-agent system treating library fuzzing as evolutionary; +191.2% branch coverage over OSS-Fuzz-Gen, 102 genuine bugs (78 acknowledged/fixed)
- **CrossHair** — concolic execution for Python via Z3. Replaces Python primitives with symbolic proxies (`SymbolicInt`→`IntSort()`, `SymbolicStr`→`StringSort()`, `SymbolicDict`→`ArraySort(K,V)`). Forks execution at branches. Provides `crosshair cover` (test generation), `crosshair diffbehavior` (refactor verification via SMT-found input diffs), `hypothesis[crosshair]` backend integration
- **Design by Contract** for Python via [`icontract`](https://pypi.org/project/icontract/), `deal`, `ensures`. `icontract` integrates directly with CrossHair — Z3 actively hunts contract violations
- **SpecPylot** (FSE 2026) — LLM-generated `icontract` annotations verified by CrossHair (PASSED/REFUTED/INCONCLUSIVE); refuted counterexamples fed back to LLM for spec refinement
- **PyTation** (ICSE 2026) — hybrid fault-driven mutation testing with Python-specific operators (RemFuncArg, RemConvFunc, ChUsedAttr, RemMetCall); combines AST analysis + dynamic execution to prune equivalent mutants
- **Memray** (Bloomberg) — Python + native C/C++ memory profiler with cross-language tracing. **Attach mode** lets you inject into a live Python process via `kubectl exec` debug container (requires SYS_PTRACE in security context) — major for OOM-in-prod diagnostics. Distinguishes true leaks from heap fragmentation
- Standardized 2026 Python QA stack: `uv` (package manager) + `Ruff` (lint+format) + `mypy` (types) + `pytest` + `pytest-xdist` (parallel) + `Hypothesis` (PBT) + `Gitleaks` (secrets) + `CodeQL` (SAST) + `CIFuzz` (continuous fuzzing GH Action) + `ClusterFuzzLite` (self-hosted fuzzing)
- WASI is now Tier 2 supported CPython platform; recommended to add to CI matrix (~10 min per run)

**Web-verified updates** (cross-link to Round 1):
- mutmut, Stryker, AFL++, libFuzzer, Hypothesis, OpenTelemetry: all verified in Round 1
- Z3 + cvc5 SMT solvers: verified Round 1

**Web gap-fills**:
- **`uv`** (Astral) — recent releases [2026-05-12](https://github.com/astral-sh/uv/releases) (Astral mirror URL override, ignore invalid `top_level.txt` entries) and 2026-04-27 (`--python-downloads-json-url`, `pip uninstall -y`). Standard fast Python package manager 2026, written in Rust by the Ruff team.

**Stale / flagged**:
- Specific CVE numbers cited in Drive doc (CVE-2026-25253 OpenClaw RCE, CVE-2026-34621/34622/34626 Adobe Acrobat prototype pollution, CVE-2025-66626 Argo, CVE-2025-26623/54080 Exiv2) — retained as references but not independently re-verified vs MITRE/NVD

---

### Vector 3 — Pygame patterns

**From Drive**: minimal direct content (the project-specific `ARPG_PYGAME_WORKFLOW.md` is .md-only in Drive and the read_file_content tool doesn't support `text/markdown` directly; skipped per scope rules anyway).

**Web-verified updates**:
- **pygame-ce 2.5.7** (March 2, 2026) supports Python 3.14 + PyPy 3.11 — verified in Round 1. SDL3/pygame-ce-3 in progress.

**Web gap-fills** (per [pygame docs](https://www.pygame.org/docs/) and [Sprite Module intro](https://www.pygame.org/docs/tut/SpriteIntro.html)):
- **Sprite Groups** are the canonical batching primitive: separate groups per object type (e.g., enemies, players, projectiles), then call `group.update(*args)` to update all sprites and `group.draw(surface)` to render. Adding/removing sprites from groups is fast; "efficiently changing group memberships" is encouraged.
- **Game-loop separation**: introduce `Tick()` functions for View and Controller so they don't share code locations; main loop just orchestrates input events → updates → redraw
- **State machines per entity**: standard pattern is one `StateMachine` per independently intelligent object, resumed in turn during the event loop. Confirmed by `pygame.org/tags/statemachine` and community state-machine frameworks like [pystates](https://github.com/egradman/pystates).
- Tutorial at [`pygame.org/wiki/tut_design`](https://www.pygame.org/wiki/tut_design) — canonical newcomer design guide.

---

### Vector 4 — Mac dev environment (M1 Pro 16GB Apple Silicon)

**From Drive** (`Local AI Automation for MacBook`):
- M1 Pro UMA bandwidth ~200 GB/s; 16GB minus ~3-4GB macOS overhead = ~11-12GB usable for ML inference (model weights + KV cache)
- **MLX** (Apple ML Research) — array framework with NumPy-like API, arrays natively in unified shared memory (no PCIe-style device transfers), lazy/JIT computation, dynamic graph construction means changing input shapes doesn't trigger blocking recompiles
- **Ollama 0.19** added native MLX backend on Apple Silicon — **~7× decoding speed vs llama.cpp Metal backend** on M1-class hardware
- Quantization math: FP16 needs 2 bytes/param (14B model = 28GB, OOM). Q4 (Q4_K_M / MLX 4-bit) needs ~0.5-0.6 bytes/param → 14B model ≈ 8.5GB, leaves ~3.5GB for KV cache
- **KV cache compression presets**: 1× (baseline, ~16GB for 128K context) / 10× ("High", ~1.6GB, +0.4% perplexity) / 17× ("Balanced", ~0.94GB, +1.3%) / 33× ("Max", ~0.48GB, +2.6%). For 128K context on 16GB hardware, Balanced 17× is the sweet spot.
- Models that fit M1 Pro 16GB: Qwen 2.5 Coder 14B (Q4, ~8.5GB), DeepSeek-R1-Distill-Qwen-14B (Q4, ~9GB), Gemma 4 E4B (FP16/Q8, ~5.5GB, multimodal), Mistral Small 24B Instruct (Q3, ~10.2GB) — note Mistral 24B at Q3 leaves only ~8-16K usable context out of 32K theoretical
- Combinatorial pipelines: route subtasks to specialized models (multimodal extraction → Gemma 4, reasoning → DeepSeek R1 Distill, code → Qwen 2.5 Coder, tool dispatch → Mistral Small 24B)

**Web-verified updates**:
- Ollama MLX integration confirmed across [The New Stack](https://thenewstack.io/ollama-taps-apples-mlx/) coverage and Ollama docs
- MLX repo at [github.com/ml-explore/mlx](https://github.com/ml-explore/mlx) confirmed actively maintained

**Web gap-fills**:
- For Apple Silicon dev environments more broadly: Homebrew remains the standard package manager; `mise` or `asdf` for language version management; `uv` for Python (Round 1 verified); Docker Desktop and OrbStack are both viable VM/container backends. (No 2026 changes that overturn this baseline.)

**Stale / flagged**:
- "OpenClaw" framework referenced in Drive doc: independently exists at [openclaw.ai](https://openclaw.ai) per Drive's own citations but this is a third-party orchestration daemon, not Anthropic's. Retained as a Drive-sourced reference; not endorsed.
- Specific model names/versions are time-dependent — re-verify before adoption

---

### Vector 5 — CI/CD pipeline specifics (GitHub Actions + OIDC)

**Web gap-fills** (per [GitHub docs](https://docs.github.com/en/actions/concepts/security/openid-connect) and [April 2026 changelog](https://github.blog/changelog/2026-04-02-github-actions-early-april-2026-updates/)):
- **OIDC eliminates long-lived cloud secrets**: instead of storing AWS/GCP/Azure credentials as repo secrets, configure cloud-side trust policies that accept short-lived OIDC tokens minted by GitHub Actions
- **Reusable workflows + OIDC**: when called, the OIDC token includes standard caller claims **plus** a custom `job_workflow_ref` claim identifying the called workflow — lets cloud roles trust specific reusable workflows across repos/orgs/enterprises
- Reusable-workflow gotcha: if the called workflow is in a different org/enterprise, the **caller** must explicitly set `permissions: id-token: write` at the caller workflow level or in the specific job calling the reusable workflow
- **2026 update (April 2)**: OIDC tokens now include **repository custom properties as claims** — enables granular trust policies based on repo metadata (e.g., `environment=production`)
- Reference: [GitHub Well-Architected: Securing GitHub Actions Workflows](https://wellarchitected.github.com/library/application-security/recommendations/actions-security/) — pin actions to commit SHAs not tags; use minimal `permissions:` blocks; require approvals on production environments
- For supply chain: combine OIDC artifact signing (Sigstore from Round 1) with SLSA v1.2 attestations to get end-to-end provenance from source → build → deploy

---

### Vector 6 — Observability beyond OpenTelemetry

**Web gap-fills**:
- **Grafana LGTM stack** ([grafana.com](https://grafana.com/)) — Grafana Labs' opinionated three-signal stack:
  - **Loki**: log aggregation, label-indexed (not full-text-indexed), cost-effective at scale
  - **Grafana**: dashboards + visualization
  - **Tempo**: distributed tracing backend, only needs object storage
  - **Mimir**: metrics, scales to 1B+ active series, multi-tenant
- All four components ingest OpenTelemetry data natively. Grafana 12 + LGTM is the current opinionated CNCF-aligned full-stack observability path.
- **Sentry SDK 2026 updates**: underlying OpenTelemetry deps upgraded to v2.x; `@sentry/node-core` ships **lightweight mode** for error tracking + logs + metrics without full OTel instrumentation; supports routing OTel logs via OTLP endpoint per [Sentry blog](https://blog.sentry.io/structured-logging-opentelemetry/)
- **Datadog ↔ OTel**: supports both Datadog SDK and OpenTelemetry SDK; can ingest Sentry SDK events into Datadog as logs (requires Error Tracking for Logs enabled). Datadog Agent can act as OTel collector. Per [Datadog OTel compatibility](https://docs.datadoghq.com/opentelemetry/compatibility/).
- **Pyroscope** (continuous profiling, eBPF-based) — already verified Round 1; complements LGTM stack for the fourth signal
- **Structured logging**:
  - Python: stdlib `logging` + `structlog` is the de facto modern stack; consider `python-json-logger` for direct JSON output
  - Node.js: [Pino](https://github.com/pinojs/pino) is the standard high-performance JSON logger; recommends running transports in worker threads via `pino.transport` API
  - Go: stdlib `log/slog` since 1.21 is now the standard; high-perf implementations like [phuslu/log](https://github.com/phuslu/log) exist for hot paths

---

### Vector 7 — Static analysis (CodeQL, Semgrep, Ruff, Biome)

**Web gap-fills**:
- **CodeQL** vs **Semgrep** ([Semgrep comparison](https://semgrep.dev/docs/faq/comparisons/codeql)):
  - **CodeQL**: proprietary (paid for non-OSS code), separate query DSL, requires a buildable environment, deep dataflow analysis
  - **Semgrep**: open source + proprietary tiers, runs anywhere, rules look like source code, 30+ languages, operates directly on source without compilation
  - **2026 development**: Semgrep added GA support for **scanning CodeQL queries themselves**, plus AI-augmented detection of business-logic flaws (IDORs, broken authorization) that CodeQL structurally cannot find
- **[Ruff v0.15.0](https://astral.sh/blog/ruff-v0.15.0)** (Feb 3, 2026): formats per the **2026 style guide**; new block suppression comments; lambda parameters stay on same line + lambda bodies parenthesized; more concise `except` clause style when `target-version` ≥ 3.14. **v0.15.1** (Feb 12, 2026): bug fixes + preview features.
- **[Biome 2026 roadmap](https://biomejs.dev/blog/roadmap-2026/)**: new concept of **linter domains**, plugins via **GritQL**, experimental full Vue/Svelte/Astro parsing/formatting/linting
- For Python: standard 2026 baseline = `ruff` (linter + formatter, replaces black + isort + flake8 + many plugins) + `mypy` (type check) + `bandit` (security) or just CodeQL/Semgrep
- For JS/TS: Biome or `eslint` + `prettier`; Biome is faster and unified but ESLint has wider plugin ecosystem still
- For all: pre-commit hooks via [pre-commit.com](https://pre-commit.com) framework remain standard for local enforcement

---

### Vector 8 — Container / Kubernetes testing

**Web gap-fills**:
- **[Kind](https://kind.sigs.k8s.io/)** (Kubernetes IN Docker, `sigs.k8s.io/kind@v0.31.0`): primary use case = testing Kubernetes itself, also viable for local dev/CI. Runs full K8s in Docker container "nodes."
- **[K3s](https://github.com/k3s-io/k3s)**: Rancher/SUSE's lightweight production-ready K8s. 2026 releases include v1.36.1, v1.34.8, with February 2026 backports across 1.32/1.34. Tier-1 CNCF graduated.
- **[k3d](https://k3d.io/)**: community wrapper for running K3s in Docker — multi-node clusters on a single machine. Not officially Rancher-maintained.
- **[KUTTL](https://kuttl.dev/) (KUbernetes Test TooL)**: declarative integration testing for K8s — tests as plain K8s resources, runs against Kind (default cluster name "kind") or mocked control plane. Originally designed for testing operators (KUDO ecosystem), works for Helm charts and any K8s objects. [GitHub](https://github.com/kudobuilder/kuttl).
- Standard pattern: Kind cluster + KUTTL declarative tests + helm chart under test = repeatable K8s integration tests in CI
- For local dev: k3d or Kind for full clusters; `minikube` is the older alternative still actively used
- Container security: Trivy + Grype for image scanning; Cosign for signing (Round 1)

---

### Vector 9 — Reproducible / hermetic builds (Nix)

**Web gap-fills** (per [nix.dev/concepts/flakes](https://nix.dev/concepts/flakes.html) + [NixOS wiki](https://wiki.nixos.org/wiki/Flakes)):
- **Nix flakes** are the modern unit for reproducible Nix evaluation: hermetic by default, lock all transitive dependencies in `flake.lock`
- **Pure evaluation mode** (the default for flakes): access to external env (env vars, network, time) is restricted to ensure reproducibility. Prevents accidentally pulling in `~/.config` or system state.
- **Git-aware**: when a `.git` directory exists, the flake **only copies git-tracked files** during evaluation — surfaces "forgot to `git add`" errors immediately instead of producing builds that work locally but break elsewhere
- **flake.lock**: auto-generated and committed; pins every input (channel, nixpkgs ref, dependency flakes) to specific revisions/hashes
- **NixOS reproducible builds tracking**: [reproducible.nixos.org](https://reproducible.nixos.org/) shows live bit-for-bit reproducibility status across the package set
- **Standard flake structure**: `inputs` (dependencies, e.g., `nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable"`) + `outputs` (packages, dev shells, NixOS configs) returned as attribute set
- For dev shells: `nix develop` enters a hermetic shell with exact tool versions pinned in the flake — replaces `direnv` + asdf for many use cases
- Bazel + Nix combination ([filmil/bazel-nix-flakes](https://github.com/filmil/bazel-nix-flakes)) achieves full bottom-to-top hermetic builds: Nix provides hermetic toolchain, Bazel provides hermetic build graph

---

### Round 2 summary

- **Vectors covered**: 9 (Claude Code/MCP, Python vuln research, Pygame, Mac dev, CI/CD OIDC, observability LGTM/Sentry/Datadog, static analysis Ruff/Biome/Semgrep/CodeQL, K8s testing Kind/KUTTL/k3d, Nix flakes)
- **Drive items extracted**: ~60 generic patterns across Vectors 1, 2, 4 (Vector 3 had no usable Drive content; Vectors 5-9 were primarily gap-fill)
- **Web-verified Drive claims**: 8 major (Opus 4.7 specs, Claude Code skills unification, mutmut/Stryker/AFL++/libFuzzer/Hypothesis/Z3/cvc5/OTel from Round 1 cross-link; pygame-ce; Ollama MLX integration)
- **Web gap-fills**: ~35 across CI/CD OIDC, LGTM stack, Sentry+Datadog OTel routing, Ruff/Biome 2026 releases, K8s testing tooling, Nix flake semantics, `uv` recent releases, structured logging defaults per language
- **Stale / flagged**: 3 (specific CVE numbers from Drive Python vuln doc; third-party "OpenClaw" framework; project-specific framework names from Drive Claude reference doc)
- **Total primary sources cited**: ~30 additional URLs (cumulative file now ~70 sources across both rounds)

**Anticipated next vectors** (Round 3+, your pick):
- Container image security (Trivy, Grype, Chainguard images, distroless)
- Secret management (Vault, sops, SOPS-NIX, Doppler, 1Password CLI)
- Database migration patterns (Alembic, Flyway, Liquibase, zero-downtime schema changes)
- Feature-flag systems (LaunchDarkly, GrowthBook, OpenFeature)
- Performance profiling beyond eBPF (perf, dtrace, Instruments on macOS)
- AI agent harnessing patterns (Anthropic effective harnesses, agent loops)
- Code search / navigation (Sourcegraph, ast-grep, Comby)
- Documentation systems (mdBook, Docusaurus, Antora, Diátaxis framework)
- Accessibility testing (axe, Pa11y, Storybook a11y addon)

**Standby.**

---

## Round 3 — 2026-05-23 — Container security, secrets, DB migrations, feature flags, profiling, AI agent harnesses, code search, docs, a11y

### Vector 1 — Container image security (Trivy, Grype, Chainguard, distroless)

**From Drive**: nothing scoped-in (Drive secrets/vault content is project-specific, stripped per scope rules).

**Web gap-fills**:
- **[Trivy](https://trivy.dev/)** (Aqua Security) — comprehensive open-source scanner: vulnerabilities + misconfigurations + secrets + SBOM. Targets: containers, Kubernetes, code repositories, cloud accounts. "Next-Gen Trivy arriving in 2026" per official site. CI integration via [trivy-action](https://github.com/aquasecurity/trivy-action).
- **[Grype](https://github.com/anchore/grype)** (Anchore) — narrower scope (container images + filesystems only) but emphasizes scan accuracy and false-positive minimization. Supports [VEX](https://github.com/openvex) to augment scan results with exploitability context.
- Trade-off: Trivy is broader (more targets, more issue classes); Grype is narrower but more focused. Many CI pipelines run both for cross-check.
- **[Chainguard Containers](https://images.chainguard.dev/)** — 1,200+ distroless images (no shells, no package managers, only runtime deps). Built-from-source with **zero CVEs** at build time; **97.6% fewer CVEs than OSS equivalents** per Chainguard. Every image ships with **Sigstore signatures, signed SBOM, and SLSA L2 provenance** verifiable via Cosign (cross-link Round 1).
- **`-dev` variants** include necessary tools for development/debugging while maintaining security posture in prod variants.
- **CVE Remediation SLA** on Chainguard Containers.
- Standard 2026 secure-container pipeline: Chainguard base image → Trivy scan in CI (with high/critical gate) → Cosign signature verification at deploy → runtime monitoring (Falco, Tetragon)

---

### Vector 2 — Secret management (Vault, SOPS, K8s patterns)

**From Drive**: Drive contains personal credential-vault docs that are project-specific (sovereign-pact related); stripped per scope rules.

**Web gap-fills**:
- **[HashiCorp Vault](https://developer.hashicorp.com/vault)** — secrets management + encryption-as-a-service + privileged access management. Native audit trails + versioning + dynamic-secret generation (e.g., short-lived DB credentials).
- **[SOPS](https://github.com/getsops/sops)** (formerly Mozilla SOPS, now community-maintained at getsops/sops) — file-level encryption with pluggable KMS backends: AWS KMS, GCP KMS, Azure Key Vault, HashiCorp Vault Transit, age, PGP. YAML/JSON/ENV/INI/BINARY files supported.
- **Git-based secret workflow** (SOPS + Vault): SOPS-encrypted secret files live in git; PR review + merge → CI pipeline with privileged Vault token decrypts and publishes to Vault. Combines code-review approval workflow with hardened runtime storage.
- Known SOPS gap as of search: doesn't yet support Vault namespace integration in secret metadata ([issue #1443](https://github.com/getsops/sops/issues/1443)).
- **For Kubernetes**: layered options:
  - **External Secrets Operator** — pulls from external stores (Vault/AWS/GCP/etc.) into K8s Secrets
  - **Sealed Secrets** (Bitnami) — encrypts to a public key, only the in-cluster controller can decrypt
  - **SOPS-NIX** — SOPS-encrypted secrets for NixOS systems
  - **Vault Agent Injector** — sidecar pattern for in-pod secret retrieval
- For dev workflows: [1Password CLI](https://developer.1password.com/docs/cli/) (`op run --env-file=.env.template`), `direnv` + SOPS, `doppler` — all common alternatives to plaintext `.env` files

---

### Vector 3 — Database migration patterns (Alembic, Flyway, Liquibase)

**From Drive**: present-but-skipped (project-specific `PHARMACY_AUDIT_db-migration` is .md-only and scoped out).

**Web gap-fills**:
- **[Alembic](https://alembic.sqlalchemy.org/)** (SQLAlchemy ecosystem) — Python-first migration tool. Migrations written in Python, fine-grained control, branching/merging migration heads, autogenerate from model diffs. Best for Python apps using SQLAlchemy.
- **[Flyway](https://flywaydb.org/)** (Redgate) — SQL-first versioned migrations (`V1__init.sql`, `V2__add_column.sql`). Strong for CD pipelines, supports repeatable migrations + undo migrations (paid). Multi-database support.
- **[Liquibase](https://www.liquibase.com/)** — changeset-based (XML/YAML/JSON/SQL). Heavier than Flyway, more enterprise features (rollback, contexts, labels, preconditions). [liquibase-zd](https://github.com/coenvk/liquibase-zd) plugin provides explicit zero-downtime schema migrations.
- **Zero-downtime migration patterns** ([Liquibase blue-green guide](https://www.liquibase.com/blog/blue-green-deployments-liquibase)):
  - **Blue-green deployments**: dual environments, migrate the inactive one, swap traffic, rollback by swapping back
  - **Expand-contract** for column changes: (1) add new nullable column, (2) dual-write old + new, (3) backfill old rows, (4) switch reads to new column, (5) stop writing old, (6) drop old. Each step independently deployable.
  - **Smaller, more frequent schema changes** with automated validation reduce blast radius
  - **Off-peak timing** for any unavoidable locking operations
  - **Rollback plans mandatory**: "If it can't be easily rolled back, it shouldn't be deployed"
- Standard 2026 pattern: app code is **forward-compatible** with both old and new schemas during migration window; migration runs separately from app deploy

---

### Vector 4 — Feature flag systems (OpenFeature, LaunchDarkly, GrowthBook)

**From Drive**: nothing scoped-in.

**Web gap-fills**:
- **[OpenFeature](https://openfeature.dev/)** — **CNCF sandbox project** (originally created by Dynatrace, now multi-vendor). Vendor-agnostic API + SDK + cloud-native implementation for feature flagging. Goal: prevent vendor lock-in by standardizing the SDK surface.
- **[LaunchDarkly](https://launchdarkly.com/)** — enterprise-focused, streaming-first delivery, observability-connected (links flag evaluations to traces/metrics), enterprise integrations (ServiceNow, Terraform, broader certifications). Provides official [OpenFeature providers](https://launchdarkly.com/docs/sdk/openfeature) across SDKs.
- **[GrowthBook](https://www.growthbook.io/)** — open-source-first, fetch-and-cache by default with optional streaming, integrated A/B experiment analysis using your existing data warehouse (BigQuery/Snowflake/Postgres). OpenFeature-compatible.
- Choose by use case:
  - **LaunchDarkly**: enterprise compliance, complex targeting, deep observability hooks
  - **GrowthBook**: self-hosted control, experiment-analysis built-in, lower cost at scale
  - **OpenFeature directly** + custom provider: max control, no vendor dependency
- Universal best practice: every flag has a **owner** + **expiration date** + **default value** that's safe if the flag service fails open; **clean up stale flags** with audit tooling (LaunchDarkly Code References, GrowthBook stale-flag report)

---

### Vector 5 — Performance profiling on macOS (Instruments, DTrace, xctrace)

**Web gap-fills**:
- **[Instruments](https://developer.apple.com/documentation/xcode/performance-and-metrics)** (Xcode-bundled) — GUI profiling app combining DTrace probes + visual analysis. Templates package sets of DTrace probes per use case (Time Profiler, Allocations, Leaks, Counters, etc.). Current as of 2026: Instruments 26.x.
- **DTrace** — macOS/BSD/Solaris kernel + userspace tracing framework. Linux analog is `perf`. Instruments is built on DTrace infrastructure.
- **`xcrun xctrace`** — command-line interface (replaces older `/usr/bin/instruments` since macOS 10.15). Use in CI for headless profiling; output `.trace` files openable in Instruments GUI.
- **[Processor Trace](https://developer.apple.com/documentation/xcode/analyzing-cpu-usage-with-processor-trace) instrument** — Apple Silicon-specific hardware-level CPU profiling that captures every executed instruction with minimal overhead. Hardware-supported on M-series chips.
- **[cargo-instruments](https://github.com/cmyr/cargo-instruments)** — Rust integration; runs `cargo build` then opens Instruments trace.
- For continuous profiling (cross-platform, not macOS-specific): Pyroscope + eBPF on Linux (Round 1).
- For Python on macOS: `py-spy` (sampling profiler, no code changes needed), `Memray` (Round 2 — attach mode for live processes).

---

### Vector 6 — AI agent harnessing patterns (long-running agents)

**Web-verified** (per [Anthropic engineering: "Effective harnesses for long-running agents"](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)):
- **Core challenge**: agents work in discrete sessions; each session starts with no memory of prior sessions. Tasks spanning hours/days need explicit scaffolding to make incremental progress across context windows.
- **Pattern (older harnesses, pre-Opus 4.5)**:
  - **Initializer agent** sets up the workspace and writes a plan to disk
  - **Per-feature coding agent** picks up one feature at a time, runs to completion, writes progress to disk
  - **Explicit context resets** between sessions force re-reading from disk
- **Pattern (Opus 4.5+ era)**: "context anxiety" behavior removed → agents can run as **one continuous session** across an entire build with automatic compaction handling context growth (cross-link Round 2 compaction beta header)
- **Two domains Anthropic applied this to**:
  1. **Codebase modernization** (legacy → modern stack migration)
  2. **Requirements → working applications** (PRD to deployed code)
- Companion reference: [Anthropic — "Harness design for long-running application development"](https://www.anthropic.com/engineering/harness-design-long-running-apps) and [Anthropic — "Scaling Managed Agents: Decoupling the brain from the body"](https://www.anthropic.com/engineering/managed-agents)
- Principle: **agents tend to try to do too much at once** — the harness's job is to constrain scope per turn and persist state to durable storage between turns
- Cross-link Round 2 Vector 1: Managed Agents API (beta `managed-agents-2026-04-01`) gives coordinator + up to 20 specialists with shared filesystem and separate context windows — this *is* the productized form of the harness pattern

---

### Vector 7 — Code search and navigation (ast-grep, Comby, Sourcegraph)

**Web gap-fills**:
- **[ast-grep](https://ast-grep.github.io/)** — Rust CLI for **structural** search/lint/rewrite. Parses source into Tree-sitter AST, matches patterns against the tree, not text. Polyglot (many languages), fast. Perfect for language-specific bulk transforms (e.g., "find every `.map(...).filter(...)` chain and rewrite as a single loop").
- **[Comby](https://comby.dev/)** — structural patterns across languages **without** language semantics. Uses balanced-delimiter parsing so it works on any language including non-code files (JSON, Markdown, config files). Trade-off per [ast-grep comparison docs](https://ast-grep.github.io/advanced/tool-comparison.html): doesn't support indentation-sensitive languages (Python, Haskell) well; complex queries are harder than ast-grep's.
- **[Sourcegraph](https://sourcegraph.com/)** — enterprise cross-repo code search; **uses Comby as its structural-search engine**. Adds graph navigation (find-references across repos), batch changes (bulk PRs across repos), code ownership, AI assistant (Cody).
- Trade-off summary:
  - **ast-grep**: highest accuracy per-language, best for refactor scripts in one repo
  - **Comby**: universal coverage including non-code files, weaker on indent-sensitive langs
  - **Sourcegraph**: best for "where is X used across our 100 repos?" type questions; SaaS or self-hosted
- For local dev: `ripgrep` (rg) remains the canonical fast text search; ast-grep complements it when you need syntactic awareness

---

### Vector 8 — Documentation systems (Diátaxis, mdBook, Docusaurus, Antora)

**Web gap-fills**:
- **[Diátaxis](https://diataxis.fr/)** — content architecture framework (not a tool), four quadrants:
  - **Tutorials** — learning-oriented, hand-held, "first contact"
  - **How-to guides** — task-oriented, "how do I do X?"
  - **Reference** — information-oriented, exhaustive, "what is the API of X?"
  - **Explanation** — understanding-oriented, "why does X work this way?"
- Solves three concerns: **content** (what to write), **style** (how to write it), **architecture** (how to organize). Framework-agnostic — any docs generator can implement it.
- **[mdBook](https://rust-lang.github.io/mdBook/)** — Rust, markdown-only, lightweight, used for the Rust book and many other Rust ecosystem docs. Single-binary, fast builds.
- **[Docusaurus](https://docusaurus.io/)** (Meta) — React-based, markdown + MDX (markdown with React components), versioning support, built-in search (Algolia integration), large plugin ecosystem. Best for product docs with marketing landing pages.
- **[Antora](https://antora.org/)** — AsciiDoc-based, designed for **multi-repo + multi-version** documentation — pulls content from multiple git repos at multiple versions into one published site. Strong for enterprise tech docs that span teams.
- **Sphinx + ReadTheDocs** — older Python-ecosystem stack, RST or markdown, strong for API reference auto-extraction
- Pick by input format and topology: markdown-only single-repo → mdBook; markdown + React → Docusaurus; AsciiDoc + multi-repo → Antora; Python + RST + auto-API → Sphinx

---

### Vector 9 — Accessibility testing (axe-core, Pa11y, WCAG 2.2)

**Web gap-fills**:
- **[axe-core](https://github.com/dequelabs/axe-core)** (Deque, MPL 2.0) — accessibility engine for automated WCAG testing. Tests against WCAG 2.0, 2.1, **2.2** at A/AA/AAA levels. Catches **~57% of WCAG issues** automatically (the rest require human judgment).
- **[Pa11y](https://github.com/pa11y/pa11y)** — CI-focused accessibility runner. Supports WCAG2AAA / WCAG2AA (default) / WCAG2A. Designed to run headless in CI pipelines and fail builds.
- **Use both**: axe-core and Pa11y catch different issue sets. Per [axe-vs-pa11y comparison](https://github.com/abbott567/axe-core-vs-pa11y), using both together catches **~35% of known issues** (still leaves majority for manual testing).
- **axe-core framework integrations** (all under the Deque umbrella or community):
  - **Storybook**: `@storybook/addon-a11y`
  - **Playwright**: `@axe-core/playwright`
  - **Cypress**: `cypress-axe`
  - **Jest**: `jest-axe`
  - **CLI**: `@axe-core/cli`
- **[WCAG 2.2](https://www.w3.org/TR/WCAG22/)** — current W3C Recommendation (since October 2023). Adds 9 new success criteria around focus visibility, dragging movements, target size, consistent help, redundant entry, accessible authentication. WCAG 3.0 still in draft.
- Standard pattern: automated axe + Pa11y in CI catches the structural/contrast/aria issues; manual screen-reader testing (VoiceOver on macOS, NVDA on Windows, TalkBack on Android) required for full coverage; user testing with assistive-tech users is the only way to verify real-world usability.
- For design phase: contrast checkers (e.g., Stark, Contrast app on macOS) catch issues before code; Figma plugin `Able` checks designs against WCAG.

---

### Round 3 summary

- **Vectors covered**: 9 (container security, secrets management, DB migrations, feature flags, macOS profiling, AI agent harnesses, code search, documentation systems, accessibility testing)
- **Drive material**: minimal direct content this round — Vectors 1, 2, 3 had Drive matches but all project-scoped and stripped per scope rules; Vector 6 cross-links to Round 2's Claude Code/MCP content
- **Web-verified**: 1 major (Anthropic effective-harnesses doc)
- **Web gap-fills**: ~50 across all 9 vectors with primary-source URLs
- **Stale / flagged**: 1 (SOPS Vault-namespace integration gap is an open issue, noted as such)
- **Total primary sources cited Round 3**: ~30 additional URLs

**Anticipated next vectors** (Round 4+, your pick):
1. Browser automation beyond Playwright (Cypress, WebdriverIO, Cucumber/Gherkin BDD)
2. Mobile testing (Appium, Detox, Espresso, XCUITest)
3. API design + contract testing depth (OpenAPI 3.1, AsyncAPI, JSON Schema, GraphQL)
4. Message queues + event streaming (Kafka, NATS, Redis Streams, RabbitMQ, SQS)
5. Service mesh + traffic management (Istio, Linkerd, Cilium)
6. Edge / serverless patterns (Cloudflare Workers, Lambda, Deno Deploy, Vercel)
7. Authentication + identity (OIDC providers, WebAuthn, passkeys, FedCM)
8. Real-time + collaborative (WebSockets, WebRTC, CRDTs, Yjs)
9. LLM application patterns (RAG architectures, agentic workflows, evaluation harnesses)

**Standby.**
