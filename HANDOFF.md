Codex Handoff: testing-kits dashboard & platform fixes (ARCHIVED)
=================================================================

> **Historical archive — do not treat as current state.**
> This handoff described work on a `codex/handoff` branch that has since been
> merged into `main` and deleted (verified 2026-06-10: the branch no longer
> exists locally or on `origin`). The changes it describes (Streamlit dashboard,
> UTF-8 report generation, Windows harness robustness) are now in `main`.
> Branch and PR references below are preserved verbatim for history only.
> For current usage, see `README.md` and `dashboard/README.md`.

Summary
-------
This branch contains the Streamlit dashboard and several fixes to make
running the harness self-tests more robust on Windows and to prevent
numeric/encoding runtime failures.

Files changed (high-level)
- `tools/generate_report.py` — Force child Python processes to use UTF-8
  (`PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`) to avoid console
  UnicodeEncodeError on Windows when harnesses print glyphs.
- `harnesses/core/memory_test_harness.py` — Make `resource` import
  optional (`resource = None` fallback) so the harness loads on Windows.
- `harnesses/core/hermeticity_test_harness.py` — Mock `USERPROFILE`
  in addition to `HOME` so `Path.home()` is affected on Windows.
- `harnesses/pharmacy/srs_test_harness.py` — Guard against
  `float('inf')` / Overflow when computing intervals.
- `dashboard/app.py` — Read inventory/status files with UTF-8 and
  `errors='replace'` to avoid decode failures.

Why
---
The repo's harnesses are run headlessly from `tools/generate_report.py`.
On Windows, console encoding and a few POSIX-only APIs caused failures
(e.g., `UnicodeEncodeError`, `ModuleNotFoundError: resource`). These
edits make the test runner and a few harnesses resilient so the
`STATUS.json`/`STATUS.md` reports can be generated locally.

How to run locally
------------------
Ensure deps installed:

```powershell
python -m pip install -r dashboard/requirements.txt
```

Generate the report (UTF-8 enforced):

```powershell
# PowerShell (recommended)
$env:PYTHONUTF8 = '1'
python tools/generate_report.py

# Or use -X utf8
python -X utf8 tools/generate_report.py
```

Run the dashboard:

```powershell
streamlit run dashboard/app.py
```

What currently still needs attention
------------------------------------
- `ai/prompt_injection` and `pharmacy/partial_fill` reported
  Unicode issues on Windows (some glyphs can't be encoded by cp1252).
  Workarounds: run with UTF-8 (above), or sanitize glyphs in the
  harness outputs.
- `core/memory` used `resource` which is POSIX-only; the harness now
  loads but some metrics may be unavailable on Windows. Prefer Linux
  CI or WSL for full fidelity.
- `core/numeric` timed out in prior runs — consider increasing
  per-harness timeout or optimize that harness.

Next steps for the reviewer
---------------------------
1. Pull the `codex/handoff` branch and run `python tools/generate_report.py`.
2. Open the Streamlit dashboard with `streamlit run dashboard/app.py`.
3. Verify `STATUS.json` appears in the repo root and ingest it in the dashboard.
4. Triage remaining failing harnesses (Unicode sanitization, timeouts).

PR details
----------
Branch: `codex/handoff`
Commit: chore: codex handoff — dashboard + Windows robustness fixes

Contact
-------
If you want me to also open the PR on GitHub and push the branch, say
so and I'll push and create the PR (I will need remote access configured
or the repository owner name if using the GitHub API).
