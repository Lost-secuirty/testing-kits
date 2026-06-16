#!/usr/bin/env python3
"""
drug_interaction_test_harness.py — Pairwise drug-interaction safety checker.
=============================================================================

Pure-stdlib + sqlite3. Zero external runtime dependencies.

Pharmacy-domain: a regimen with N active medications has C(N,2) drug-drug
pairs, plus food/drug interactions, plus condition contraindications. The
checker must:
  - Block CONTRAINDICATED pairs on Rx-add.
  - Warn on SEVERE pairs and require an audited override.
  - Escalate severity on multi-drug overlaps (e.g. two QT-prolonging drugs +
    a third weak-prolonger becomes SEVERE).
  - Persist override audit trail (who, when, reason).

Usage:
  python harnesses/pharmacy/drug_interaction_test_harness.py --self-test
  python harnesses/pharmacy/drug_interaction_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------


class Severity(IntEnum):
    NONE = 0
    MILD = 1
    MODERATE = 2
    SEVERE = 3
    CONTRAINDICATED = 4


@dataclass(frozen=True)
class Interaction:
    drug_a: str
    drug_b: str
    severity: Severity
    mechanism: str = ""
    override_allowed: bool = True


@dataclass(frozen=True)
class FoodInteraction:
    drug: str
    food: str
    severity: Severity
    mechanism: str = ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class InteractionEngine:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self._init_schema()

    def _init_schema(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS interactions (
                drug_a TEXT NOT NULL,
                drug_b TEXT NOT NULL,
                severity INTEGER NOT NULL,
                mechanism TEXT NOT NULL DEFAULT '',
                override_allowed INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (drug_a, drug_b)
            );
            CREATE TABLE IF NOT EXISTS food_interactions (
                drug TEXT NOT NULL,
                food TEXT NOT NULL,
                severity INTEGER NOT NULL,
                mechanism TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (drug, food)
            );
            CREATE TABLE IF NOT EXISTS override_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drug_a TEXT NOT NULL,
                drug_b TEXT NOT NULL,
                severity INTEGER NOT NULL,
                role TEXT NOT NULL,
                reason TEXT NOT NULL,
                at TEXT NOT NULL
            );
        """)
        self.db.commit()

    def load_interaction(self, ix: Interaction) -> None:
        a, b = sorted([ix.drug_a, ix.drug_b])  # canonicalize order
        self.db.execute(
            "INSERT OR REPLACE INTO interactions VALUES (?,?,?,?,?)",
            (a, b, int(ix.severity), ix.mechanism, int(ix.override_allowed)),
        )
        self.db.commit()

    def load_food_interaction(self, fx: FoodInteraction) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO food_interactions VALUES (?,?,?,?)",
            (fx.drug, fx.food, int(fx.severity), fx.mechanism),
        )
        self.db.commit()

    def lookup_pair(self, drug_a: str, drug_b: str) -> Interaction | None:
        a, b = sorted([drug_a, drug_b])
        row = self.db.execute(
            "SELECT drug_a, drug_b, severity, mechanism, override_allowed "
            "FROM interactions WHERE drug_a=? AND drug_b=?", (a, b)
        ).fetchone()
        if not row:
            return None
        return Interaction(
            drug_a=row[0], drug_b=row[1],
            severity=Severity(row[2]),
            mechanism=row[3], override_allowed=bool(row[4]),
        )

    def scan_regimen(self, drugs: list[str]) -> list[Interaction]:
        """Return all interactions among the regimen, pairwise."""
        out: list[Interaction] = []
        for a, b in itertools.combinations(drugs, 2):
            ix = self.lookup_pair(a, b)
            if ix and ix.severity > Severity.NONE:
                out.append(ix)
        return out

    def scan_food(self, drugs: list[str], foods: list[str]) -> list[FoodInteraction]:
        out: list[FoodInteraction] = []
        for d in drugs:
            for f in foods:
                row = self.db.execute(
                    "SELECT drug, food, severity, mechanism "
                    "FROM food_interactions WHERE drug=? AND food=?",
                    (d, f),
                ).fetchone()
                if row:
                    out.append(FoodInteraction(
                        drug=row[0], food=row[1],
                        severity=Severity(row[2]), mechanism=row[3],
                    ))
        return out

    def escalate_qt_prolongation(self, drugs: list[str],
                                  qt_drugs: set[str]) -> Severity:
        """If 2+ QT-prolonging drugs co-prescribed, returns SEVERE."""
        count = sum(1 for d in drugs if d in qt_drugs)
        if count >= 3:
            return Severity.CONTRAINDICATED
        if count >= 2:
            return Severity.SEVERE
        return Severity.NONE

    def can_add(self, current: list[str], new_drug: str) -> tuple[bool, list[Interaction]]:
        """Return (allowed, issues). Allowed=False iff any CONTRAINDICATED."""
        issues = self.scan_regimen(current + [new_drug])
        blockers = [ix for ix in issues if ix.severity == Severity.CONTRAINDICATED]
        return (not blockers, issues)

    def record_override(self, drug_a: str, drug_b: str, severity: Severity,
                        role: str, reason: str) -> int:
        a, b = sorted([drug_a, drug_b])
        cur = self.db.execute(
            "INSERT INTO override_audit (drug_a, drug_b, severity, role, reason, at) "
            "VALUES (?,?,?,?,?,?)",
            (a, b, int(severity), role, reason,
             dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        self.db.commit()
        return cur.lastrowid or 0

    def list_overrides(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, drug_a, drug_b, severity, role, reason, at "
            "FROM override_audit ORDER BY id"
        ).fetchall()
        return [{"id": r[0], "drug_a": r[1], "drug_b": r[2],
                 "severity": r[3], "role": r[4], "reason": r[5], "at": r[6]}
                for r in rows]


# ---------------------------------------------------------------------------
# Self-test scenarios
# ---------------------------------------------------------------------------


def _seed_engine() -> InteractionEngine:
    conn = sqlite3.connect(":memory:")
    eng = InteractionEngine(conn)
    eng.load_interaction(Interaction("warfarin", "aspirin", Severity.SEVERE,
                                     "bleeding risk"))
    eng.load_interaction(Interaction("maoi", "ssri", Severity.CONTRAINDICATED,
                                     "serotonin syndrome", override_allowed=False))
    eng.load_interaction(Interaction("amox", "ibuprofen", Severity.MILD))
    eng.load_food_interaction(FoodInteraction("warfarin", "grapefruit",
                                              Severity.MODERATE))
    eng.load_food_interaction(FoodInteraction("statin", "grapefruit",
                                              Severity.SEVERE,
                                              "rhabdomyolysis risk"))
    return eng


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str = ""


def scenario_pair_lookup() -> ScenarioResult:
    eng = _seed_engine()
    ix = eng.lookup_pair("aspirin", "warfarin")  # reverse order
    return ScenarioResult(
        name="pair_lookup_canonical",
        passed=ix is not None and ix.severity == Severity.SEVERE,
        detail=f"got={ix}",
    )


def scenario_contraindicated_blocks() -> ScenarioResult:
    eng = _seed_engine()
    allowed, issues = eng.can_add(["maoi"], "ssri")
    return ScenarioResult(
        name="contraindicated_blocks_add",
        passed=not allowed and any(i.severity == Severity.CONTRAINDICATED for i in issues),
        detail=f"allowed={allowed}, issues={len(issues)}",
    )


def scenario_severe_warns_but_allows() -> ScenarioResult:
    eng = _seed_engine()
    allowed, issues = eng.can_add(["warfarin"], "aspirin")
    return ScenarioResult(
        name="severe_warns_but_allows",
        passed=allowed and any(i.severity == Severity.SEVERE for i in issues),
        detail=f"allowed={allowed}, issues={len(issues)}",
    )


def scenario_food_interaction() -> ScenarioResult:
    eng = _seed_engine()
    fxs = eng.scan_food(["statin"], ["grapefruit", "milk"])
    return ScenarioResult(
        name="food_interaction_detected",
        passed=any(f.severity == Severity.SEVERE for f in fxs),
        detail=f"interactions={len(fxs)}",
    )


def scenario_regimen_scan_finds_all_pairs() -> ScenarioResult:
    eng = _seed_engine()
    issues = eng.scan_regimen(["warfarin", "aspirin", "amox", "ibuprofen"])
    # warfarin-aspirin SEVERE, amox-ibuprofen MILD; that's 2 known pairs.
    return ScenarioResult(
        name="regimen_scan_pairwise",
        passed=len(issues) == 2,
        detail=f"issues={[(i.drug_a, i.drug_b, i.severity.name) for i in issues]}",
    )


def scenario_qt_escalation() -> ScenarioResult:
    eng = _seed_engine()
    qt_drugs = {"ondansetron", "haloperidol", "azithromycin"}
    sev_two = eng.escalate_qt_prolongation(["ondansetron", "haloperidol"], qt_drugs)
    sev_three = eng.escalate_qt_prolongation(
        ["ondansetron", "haloperidol", "azithromycin"], qt_drugs)
    return ScenarioResult(
        name="qt_escalation",
        passed=sev_two == Severity.SEVERE and sev_three == Severity.CONTRAINDICATED,
        detail=f"two={sev_two.name}, three={sev_three.name}",
    )


def scenario_override_audit_persists() -> ScenarioResult:
    eng = _seed_engine()
    eng.record_override("warfarin", "aspirin", Severity.SEVERE,
                        role="pharmacist", reason="monitor INR weekly")
    overrides = eng.list_overrides()
    return ScenarioResult(
        name="override_audit_persists",
        passed=len(overrides) == 1 and overrides[0]["role"] == "pharmacist",
        detail=f"overrides={overrides}",
    )


def scenario_non_overridable_contraindication() -> ScenarioResult:
    eng = _seed_engine()
    ix = eng.lookup_pair("maoi", "ssri")
    return ScenarioResult(
        name="non_overridable_contraindication",
        passed=ix is not None and not ix.override_allowed,
        detail=f"override_allowed={ix.override_allowed if ix else None}",
    )


SCENARIOS: dict[str, Callable[[], ScenarioResult]] = {
    "pair_lookup_canonical": scenario_pair_lookup,
    "contraindicated_blocks_add": scenario_contraindicated_blocks,
    "severe_warns_but_allows": scenario_severe_warns_but_allows,
    "food_interaction_detected": scenario_food_interaction,
    "regimen_scan_pairwise": scenario_regimen_scan_finds_all_pairs,
    "qt_escalation": scenario_qt_escalation,
    "override_audit_persists": scenario_override_audit_persists,
    "non_overridable_contraindication": scenario_non_overridable_contraindication,
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    for r in results:
        mark = "OK  " if r.passed else "FAIL"
        print(f"  {mark}  {r.name:35s} {r.detail}")
    failures = [r for r in results if not r.passed]
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Drug-drug & food-drug interaction harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
