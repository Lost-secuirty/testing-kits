#!/usr/bin/env python3
"""Import one harness, run its ``TEETH`` swap-check, and print the result as JSON.

Run as a subprocess (one per harness) by ``tools/proof_audit.py`` so a harness that
crashes, hangs, or ``sys.exit``s at import cannot poison the auditor. Stdlib only.

Exit codes: 0 = no TEETH (pending) or TEETH verified; 1 = TEETH present but not
verified; 2 = import error / usage error.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def check(target: Path) -> tuple[dict, int]:
    if not target.is_absolute():
        target = REPO_ROOT / target
    result: dict = {
        "module": None,
        "import_ok": False,
        "teeth_present": False,
        "teeth_verified": False,
        "error": None,
    }
    try:
        rel = target.resolve().relative_to(REPO_ROOT).with_suffix("")
    except ValueError:
        result["error"] = f"path outside repo: {target}"
        return result, 2
    modpath = ".".join(rel.parts)
    result["module"] = modpath
    if rel.parts[0] != "harnesses":
        result["error"] = f"refusing to import non-harness path: {modpath}"
        return result, 2

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        mod = importlib.import_module(modpath)
    except Exception as exc:  # noqa: BLE001 — any import failure is reported, not raised
        result["error"] = f"import: {type(exc).__name__}: {exc}"[:300]
        return result, 2
    result["import_ok"] = True

    teeth = getattr(mod, "TEETH", None)
    if teeth is None:
        return result, 0  # pending: importable, no teeth declared yet

    from harnesses._teeth import Teeth, verify

    result["teeth_present"] = True
    if not isinstance(teeth, Teeth):
        result["error"] = f"TEETH is {type(teeth).__name__}, expected Teeth"
        return result, 1
    result.update(verify(teeth))
    result["teeth_present"] = True
    return result, (0 if result["teeth_verified"] else 1)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(json.dumps({"error": "usage: teeth_check.py <harness_path>"}))
        return 2
    result, code = check(Path(argv[0]))
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
