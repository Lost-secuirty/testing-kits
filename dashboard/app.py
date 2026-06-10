#!/usr/bin/env python3
"""
Harness Test Dashboard — Streamlit app for testing-kits
Run with: streamlit run dashboard/app.py
"""

import subprocess
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd

try:
    from git import Repo
    GIT_AVAILABLE = True
except Exception:
    GIT_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parent.parent
STATUS_MD = REPO_ROOT / "STATUS.md"
STATUS_JSON = REPO_ROOT / "STATUS.json"
INVENTORY_MD = REPO_ROOT / "HARNESS_INVENTORY.md"

st.set_page_config(page_title="testing-kits Dashboard", layout="wide")
st.title("🧪 testing-kits Test Dashboard")
st.caption("Interactive runner to verify harness self-tests and unittests. Built with Streamlit.")

def load_status():
    if STATUS_JSON.exists():
        try:
            return json.loads(STATUS_JSON.read_text(encoding="utf-8")), "json"
        except Exception:
            pass
    if STATUS_MD.exists():
        try:
            text = STATUS_MD.read_text(encoding="utf-8")
            if "## Per harness" in text:
                part = text.split("## Per harness", 1)[1]
                lines = [ln.strip() for ln in part.splitlines() if ln.strip()]
                rows = []
                for ln in lines:
                    if ln.startswith("|"):
                        cols = [c.strip() for c in ln.split("|")][1:-1]
                        if len(cols) >= 4:
                            rows.append(cols)
                per_harness = []
                for cols in rows:
                    name = cols[0]
                    status = cols[1] if len(cols) > 1 else ""
                    duration = cols[2].rstrip("s") if len(cols) > 2 else ""
                    tail = cols[3] if len(cols) > 3 else ""
                    if "/" in name:
                        cat, nm = name.split("/", 1)
                    else:
                        cat, nm = "(unknown)", name
                    try:
                        duration_f = float(duration)
                    except Exception:
                        duration_f = 0.0
                    per_harness.append({"category": cat, "name": nm, "status": status, "duration_s": duration_f, "tail": tail})
                return {"generated_at": "", "summary": {}, "by_category": {}, "per_harness": per_harness}, "md"
        except Exception:
            pass
    return None, None

def run_cmd(cmd, timeout=300):
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"

# Sidebar
with st.sidebar:
    st.header("Quick Actions")
    allow_exec = st.checkbox("Allow execution from this dashboard", value=False)
    timeout = st.number_input("Command timeout (s)", min_value=30, max_value=3600, value=300, step=30)

    if st.button("🔄 Run Full Selftest (all harnesses)"):
        if not allow_exec:
            st.warning("Enable 'Allow execution' to run the selftest.")
        else:
            with st.spinner("Running selftest... (this can take several minutes)"):
                rc, out = run_cmd([sys.executable, str(REPO_ROOT / "tools" / "generate_report.py")], timeout=timeout)
                st.session_state["last_selftest_output"] = out
                st.session_state["last_selftest_return"] = rc
                if rc == 0:
                    st.success("Selftest completed!")
                else:
                    st.error(f"Selftest finished with exit code {rc}")

    if st.button("🧪 Run unit tests (make test / fallback)"):
        if not allow_exec:
            st.warning("Enable 'Allow execution' to run unit tests.")
        else:
            with st.spinner("Running unit tests..."):
                if shutil.which("make"):
                    cmd = ["make", "test"]
                else:
                    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py"]
                rc, out = run_cmd(cmd, timeout=timeout)
                st.session_state["last_test_output"] = out
                st.session_state["last_test_return"] = rc
                if rc == 0:
                    st.success("Unit tests completed")
                else:
                    st.error(f"Unit tests finished with exit code {rc}")

    if st.button("📊 Regenerate STATUS only"):
        if not allow_exec:
            st.warning("Enable 'Allow execution' to regenerate STATUS files.")
        else:
            rc, out = run_cmd([sys.executable, str(REPO_ROOT / "tools" / "generate_report.py")], timeout=timeout)
            st.session_state["last_selftest_output"] = out
            st.session_state["last_selftest_return"] = rc
            if rc == 0:
                st.success("STATUS.json and STATUS.md refreshed")
            else:
                st.error(f"Report generation failed (rc={rc})")

    st.divider()
    st.caption("Repo health")
    if GIT_AVAILABLE:
        try:
            repo = Repo(REPO_ROOT)
            st.write(f"**Branch:** {getattr(repo, 'active_branch', 'unknown')}")
            st.write(f"**Last commit:** {repo.head.commit.hexsha[:8]}")
            st.write(f"**Dirty:** {repo.is_dirty()}")
        except Exception:
            st.write("Git info unavailable")
    else:
        st.caption("Install GitPython for repo status")

# Main content
tab1, tab2, tab3, tab4 = st.tabs(["📈 Live Results", "📚 Harness Inventory", "📝 STATUS.md", "🛠️ Logs & Output"])

with tab1:
    st.subheader("Latest Selftest Results")
    data, source = load_status()
    if data is None:
        st.info("No STATUS.json or STATUS.md found. Click 'Run Full Selftest' in the sidebar.")
    else:
        st.write(f"Data source: {source}")
        gen_at = data.get("generated_at", "")
        if gen_at:
            st.caption(f"Generated: {gen_at}")
        summary = data.get("summary", {})
        proof_summary = data.get("proof", {}).get("summary", {})
        if summary or proof_summary:
            cols = st.columns(4)
            cols[0].metric("Harnesses", summary.get("total_harnesses", "-"))
            cols[1].metric("Self-test OK", summary.get("ok", "-"))
            cols[2].metric("Proof OK", proof_summary.get("ok", "-"))
            cols[3].metric("Proof gaps", proof_summary.get("fail", "-"))
        per_harness = data.get("per_harness", [])
        df = pd.DataFrame(per_harness)
        if not df.empty:
            st.dataframe(df, use_container_width=True)
            categories = sorted(df["category"].unique())
            cat_filter = st.multiselect("Filter categories", options=categories, default=categories)
            status_filter = st.multiselect("Filter status", options=sorted(df["status"].unique()), default=sorted(df["status"].unique()))
            filtered = df[df["category"].isin(cat_filter) & df["status"].isin(status_filter)]
            st.dataframe(filtered, use_container_width=True)
            try:
                st.bar_chart(filtered.set_index("name")["duration_s"].sort_values(ascending=False))
            except Exception:
                pass
        else:
            st.info("No per-harness entries found.")

with tab2:
    st.subheader("Harness Browser (from HARNESS_INVENTORY.md)")
    if INVENTORY_MD.exists():
        inv_text = INVENTORY_MD.read_text(encoding="utf-8", errors="replace")
        search = st.text_input("Search harnesses (name, category, or keyword)", "")
        if search:
            filtered = [line for line in inv_text.splitlines() if search.lower() in line.lower()]
            st.code("\n".join(filtered[:200]), language="markdown")
        else:
            st.markdown(inv_text[:8000] + "\n\n... (full file is long — use search above)")
    else:
        st.warning("HARNESS_INVENTORY.md not found")

with tab3:
    st.subheader("Current STATUS.md (auto-generated)")
    if STATUS_MD.exists():
        st.markdown(STATUS_MD.read_text(encoding="utf-8", errors="replace"))
    else:
        st.info("Run selftest to generate it.")

with tab4:
    st.subheader("Last Command Output")
    if "last_selftest_output" in st.session_state:
        st.code(st.session_state.last_selftest_output, language="bash")
        if st.session_state.get("last_selftest_return") == 0:
            st.success("All green ✅")
        else:
            st.error("Some failures or skips")

    if "last_test_output" in st.session_state:
        with st.expander("make test output"):
            st.code(st.session_state.last_test_output, language="bash")

    st.divider()
    st.subheader("Run single harness")
    harness_files = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in REPO_ROOT.glob("harnesses/*/*.py")
        if p.name != "__init__.py"
    )
    harness_choice = st.selectbox("Select harness (relative path)", options=[""] + harness_files, index=0)
    if harness_choice:
        if st.button("Run selected harness --self-test"):
            if not allow_exec:
                st.warning("Enable 'Allow execution' to run the harness.")
            else:
                rc, out = run_cmd([sys.executable, str(REPO_ROOT / harness_choice), "--self-test"], timeout=timeout)
                st.code(out, language="bash")
                if rc == 0:
                    st.success("Harness self-test OK")
                else:
                    st.error(f"Harness returned {rc}")

st.caption(f"Dashboard generated at {datetime.now().strftime('%Y-%m-%d %H:%M')} | testing-kits portfolio project")
