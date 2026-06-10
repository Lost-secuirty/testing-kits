# testing-kits Dashboard

This Streamlit app provides a developer-facing dashboard for running the testing-kits harness self-tests and viewing `STATUS.md` / `STATUS.json` results.

Quick start

1. Create and activate a virtual environment (recommended):

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
```

2. Install dependencies:

```bash
python -m pip install -r dashboard/requirements.txt
```

3. Generate an initial report (optional):

```bash
python tools/generate_report.py
```

4. Run the dashboard:

```bash
streamlit run dashboard/app.py
```

Notes

- The dashboard requires explicit confirmation to run tests from the UI. Use the sidebar checkboxes to enable execution.
- The app prefers `STATUS.json` when available and falls back to `STATUS.md`.
