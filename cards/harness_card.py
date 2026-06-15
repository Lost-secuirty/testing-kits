#!/usr/bin/env python3
"""TestHarnessCard — per-harness cards + a teeth-status RATCHET over the live gate.

STAGED / ADDITIVE. This lives in its own ``cards/`` directory and touches no existing
file. It is meant to be wired into the gate AFTER the Batch 0 teeth campaign lands (see
``cards/README.md`` for the one-line CI step). Until then it is run on demand.

It does two things the existing gate (``tools/proof_audit.py``) does not:

1. **A committed per-harness CARD.** The gate's per-harness JSON is regenerated and
   gitignored (STATUS.json); there is no committed, reviewable card. This consolidates
   each harness's distributed metadata — what it IS (docstring), what it CATCHES
   (teeth kind / mutants / corpus), and how it is TESTED (paired test, proof, self-test)
   — into ``cards/cards.json`` + ``cards/CARDS.md``.

2. **A teeth RATCHET.** The gate derives ``required`` purely from a live ``TEETH``
   declaration, so a harness that LOSES its ``TEETH`` silently drops to the
   non-blocking ``pending`` scope — a regression the gate does not flag. The committed
   ``cards/teeth_ratchet.json`` pins the status each harness has earned; ``--check``
   fails loud if any harness's live status ranks BELOW its pin (status may only ratchet
   up, never silently down).

Run:
  python cards/harness_card.py --write --update-ratchet   # regenerate cards + raise pins
  python cards/harness_card.py --check                    # gate: fail on a teeth regression
  python cards/harness_card.py --self-test                # prove this tool has teeth
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.harness_registry import discover_harnesses  # noqa: E402
from tools.proof_audit import audit_harnesses  # noqa: E402

CARDS_JSON = _HERE / "cards.json"
CARDS_MD = _HERE / "CARDS.md"
RATCHET_PATH = _HERE / "teeth_ratchet.json"

# A harness may only ratchet UP this ladder, never silently fall back.
_RANK = {"legacy": 0, "pending": 1, "required": 2}


def _purpose(path: Path) -> str:
    """First meaningful line of the module docstring — what the harness IS."""
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8", errors="replace"))) or ""
    except (SyntaxError, OSError):
        return ""
    for line in doc.splitlines():
        cleaned = line.strip().strip("=").strip()
        if cleaned:
            return cleaned
    return ""


def build_cards(*, run_teeth: bool = True) -> list[dict]:
    """One card per harness, merging the live gate state with the docstring purpose."""
    records = discover_harnesses()
    rec_by_key = {r.key: r for r in records}
    audit = audit_harnesses(records, run_teeth=run_teeth)
    cards = []
    for row in sorted(audit["per_harness"], key=lambda r: r["key"]):
        rec = rec_by_key[row["key"]]
        cards.append({
            "key": row["key"],
            "category": row["category"],
            "purpose": _purpose(rec.path),
            "harness_path": row["path"],
            # what the harness CATCHES (the system-under-test side)
            "teeth_status": row["scope"],
            "teeth_verified": bool(row.get("teeth_verified")),
            "teeth_kind": row.get("teeth_kind"),
            "mutants_total": int(row.get("mutants_total") or 0),
            "mutants_caught": int(row.get("mutants_caught") or 0),
            "mutants_uncaught": list(row.get("mutants_uncaught") or []),
            "corpus_size": int(row.get("corpus_size") or 0),
            # how the harness ITSELF is tested (the proof side)
            "paired_test": row["paired_test"],
            "paired_test_exists": bool(row["paired_test_exists"]),
            "proof_test": row["proof_test"],
            "proof_test_exists": bool(row["proof_test_exists"]),
            "selftest_status": row.get("selftest_status"),
            "ok": bool(row["ok"]),
            "failures": list(row.get("failures") or []),
        })
    return cards


def load_ratchet() -> dict:
    if RATCHET_PATH.exists():
        return json.loads(RATCHET_PATH.read_text(encoding="utf-8"))
    return {}


def check_ratchet(cards: list[dict], ratchet: dict) -> list[str]:
    """Return drift violations (empty == clean).

    Flags: a live teeth status ranked BELOW its pin; a pinned-but-vanished harness;
    an unrecognized pin value (a hand-edit typo that would otherwise silently disable
    the ratchet); a ratcheted-required missing its paired test; and a required harness
    that declares no mutants or no longer catches all of them.
    """
    problems: list[str] = []
    live = {c["key"]: c for c in cards}
    for key, pinned in sorted(ratchet.items()):
        if pinned not in _RANK:
            problems.append(
                f"{key}: invalid pin '{pinned}' (expected one of {sorted(_RANK)}) — "
                "a typo here would silently disable the ratchet for this harness"
            )
            continue
        card = live.get(key)
        if card is None:
            problems.append(f"{key}: ratcheted '{pinned}' but the harness no longer exists")
            continue
        status = card["teeth_status"]
        if _RANK.get(status, -1) < _RANK[pinned]:
            problems.append(
                f"{key}: teeth REGRESSED to '{status}' but is ratcheted at '{pinned}' "
                "(a harness may not silently fall back below the status it earned)"
            )
        if pinned == "required" and not card["paired_test_exists"]:
            problems.append(f"{key}: ratcheted 'required' but its paired unittest is missing")
        if status == "required":
            total, caught = card["mutants_total"], card["mutants_caught"]
            if total == 0:
                problems.append(f"{key}: required but declares no mutants (teeth has nothing to catch)")
            elif caught < total:
                problems.append(f"{key}: required but only {caught}/{total} mutants caught")
    return problems


def _render_md(cards: list[dict]) -> str:
    by_cat: dict[str, list[dict]] = {}
    for card in cards:
        by_cat.setdefault(card["category"], []).append(card)
    counts = {s: sum(1 for c in cards if c["teeth_status"] == s) for s in ("required", "pending", "legacy")}
    lines = [
        "# Test-Harness Cards",
        "",
        "Generated by `cards/harness_card.py --write` — one card per harness, merging the live "
        "TEETH gate (`tools/proof_audit.py`) with each harness's docstring. Do not hand-edit; the "
        "committed `teeth_ratchet.json` is the status pin the `--check` gate enforces.",
        "",
        f"**{len(cards)} harnesses** — required {counts['required']}, pending {counts['pending']}, "
        f"legacy {counts['legacy']}.",
        "",
    ]
    for category in sorted(by_cat):
        lines.append(f"## {category}")
        lines.append("")
        for card in sorted(by_cat[category], key=lambda c: c["key"]):
            mark = {"required": "✓ required", "pending": "… pending", "legacy": "— legacy"}[card["teeth_status"]]
            lines.append(f"### {card['key']} · {mark}")
            lines.append("")
            if card["purpose"]:
                lines.append(f"- **Is:** {card['purpose']}")
            if card["teeth_status"] == "required":
                lines.append(
                    f"- **Catches:** {card['teeth_kind'] or 'oracle_swap'} · "
                    f"{card['mutants_caught']}/{card['mutants_total']} mutants caught · "
                    f"corpus {card['corpus_size']}"
                )
            tested = "paired ✓" if card["paired_test_exists"] else "paired ✗"
            if card["proof_test_exists"]:
                tested += " · proof ✓"
            if card["selftest_status"]:
                tested += f" · self-test {card['selftest_status']}"
            lines.append(f"- **Tested by:** {tested}")
            lines.append("")
    return "\n".join(lines)


def write_artifacts(cards: list[dict], *, update_ratchet: bool) -> None:
    CARDS_JSON.write_text(json.dumps(cards, indent=2) + "\n", encoding="utf-8")
    CARDS_MD.write_text(_render_md(cards), encoding="utf-8")
    if update_ratchet:
        ratchet = load_ratchet()
        for card in cards:
            key, status = card["key"], card["teeth_status"]
            if _RANK.get(status, 0) > _RANK.get(ratchet.get(key, "legacy"), 0):
                ratchet[key] = status            # raise the pin only
            ratchet.setdefault(key, status)
        RATCHET_PATH.write_text(json.dumps(dict(sorted(ratchet.items())), indent=2) + "\n", encoding="utf-8")


def _run_self_test() -> int:
    """Prove the ratchet has teeth: a planted regression MUST be caught, a clean state MUST pass."""
    failures = 0
    ratchet = {"core/a": "required", "core/b": "required", "core/c": "pending"}
    regressed = [
        {"key": "core/a", "teeth_status": "required", "paired_test_exists": True,
         "mutants_total": 2, "mutants_caught": 2},
        {"key": "core/b", "teeth_status": "pending", "paired_test_exists": True,   # lost its TEETH
         "mutants_total": 0, "mutants_caught": 0},
        {"key": "core/c", "teeth_status": "pending", "paired_test_exists": True,
         "mutants_total": 0, "mutants_caught": 0},
    ]
    problems = check_ratchet(regressed, ratchet)
    if not any("core/b" in p and "REGRESSED" in p for p in problems):
        failures += 1
        print("FAIL: ratchet did not catch a required->pending regression", file=sys.stderr)
    if any("core/a" in p or "core/c" in p for p in problems):
        failures += 1
        print("FAIL: ratchet flagged a harness that did not regress", file=sys.stderr)

    clean = [
        {"key": "core/a", "teeth_status": "required", "paired_test_exists": True,
         "mutants_total": 2, "mutants_caught": 2},
        {"key": "core/b", "teeth_status": "required", "paired_test_exists": True,
         "mutants_total": 1, "mutants_caught": 1},
        {"key": "core/c", "teeth_status": "pending", "paired_test_exists": True,
         "mutants_total": 0, "mutants_caught": 0},
    ]
    if check_ratchet(clean, ratchet):
        failures += 1
        print("FAIL: ratchet flagged a clean (non-regressed) state", file=sys.stderr)

    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (regression caught; clean state passes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TestHarnessCard — cards + teeth ratchet")
    parser.add_argument("--write", action="store_true", help="regenerate cards.json + CARDS.md")
    parser.add_argument("--update-ratchet", action="store_true", help="raise pins to current status")
    parser.add_argument("--check", action="store_true", help="gate: fail on a teeth regression")
    parser.add_argument("--self-test", action="store_true", help="prove this tool has teeth")
    parser.add_argument("--no-teeth", action="store_true", help="skip the swap-check (faster, status only)")
    parser.add_argument("--json", action="store_true", help="print the cards as JSON")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    cards = build_cards(run_teeth=not args.no_teeth)

    if args.write or args.update_ratchet:
        write_artifacts(cards, update_ratchet=args.update_ratchet)
        print(f"wrote {CARDS_JSON.name}, {CARDS_MD.name}"
              + (f", {RATCHET_PATH.name}" if args.update_ratchet else ""))

    if args.check:
        ratchet = load_ratchet()
        if not ratchet:
            print("no teeth_ratchet.json yet — run --update-ratchet first", file=sys.stderr)
            return 2
        problems = check_ratchet(cards, ratchet)
        if problems:
            print(f"teeth ratchet: {len(problems)} regression(s):", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(f"teeth ratchet: OK ({len(ratchet)} harnesses pinned, none regressed)")

    if args.json:
        print(json.dumps(cards, indent=2))
    if not (args.write or args.update_ratchet or args.check or args.json):
        parser.print_help(sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
