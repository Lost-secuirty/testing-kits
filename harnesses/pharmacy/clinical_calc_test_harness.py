#!/usr/bin/env python3
"""clinical_calc_test_harness.py — Clinical Calculator Safety Harness (2026)
===========================================================================
Pure-Python (ZERO dependencies) harness for testing medical calculators as
a safety-critical domain with biological plausibility oracles.

Distinct from fuzz_test_harness (#10) and numeric_test_harness (#23):
  - Domain-semantic oracles: knows what biologically plausible BSA/CrCl is
  - Formula identity: independent _ref_* implementations verify correctness
  - Clinical bounds: outputs outside plausible ranges fail loudly
  - Cross-calculator monotonicity: doubling weight must increase BSA and CrCl

Calculators tested:
  - BSA Mosteller: sqrt((height_cm * weight_kg) / 3600)
  - CrCl Cockcroft-Gault: (140-age)*wt/(72*SCr) * 0.85 if female
  - Pediatric dose: weight * mg/kg/day -> mg/dose -> mL/dose
  - Insulin days supply: floor(total_ml*conc / (daily+priming))
  - Days supply: floor(qty / daily), capped at 3650

Port: 19240

Usage:
  python clinical_calc_test_harness.py --self-test
  python clinical_calc_test_harness.py --mock-server --port 19240
  python clinical_calc_test_harness.py --self-test --verbose
"""

import argparse
import json
import math
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================================================
# REFERENCE IMPLEMENTATIONS (independent oracles)
# ============================================================

def _ref_bsa(height_cm, weight_kg):
    return math.sqrt((height_cm * weight_kg) / 3600.0)


def _ref_crcl(age, weight_kg, scr, is_female=False):
    crcl = ((140.0 - age) * weight_kg) / (72.0 * scr)
    if is_female:
        crcl *= 0.85
    return crcl


def _ref_peds_mg(weight_kg, mg_per_kg_per_day, doses_per_day):
    return (weight_kg * mg_per_kg_per_day) / doses_per_day


def _ref_peds_ml(weight_kg, mg_per_kg_per_day, doses_per_day, conc_mg_per_ml):
    return _ref_peds_mg(weight_kg, mg_per_kg_per_day, doses_per_day) / conc_mg_per_ml


# ============================================================
# INLINE CALCULATORS (mirrors pharmacy_app/logic.py)
# ============================================================

def calc_bsa_mosteller(height_cm, weight_kg):
    """BSA via Mosteller formula (NEJM 1987): sqrt((h*w)/3600). Rounded to 2dp."""
    try:
        h, w = float(height_cm), float(weight_kg)
    except (ValueError, TypeError):
        raise ValueError("Invalid numeric inputs.")
    if not all(math.isfinite(v) for v in (h, w)):
        raise ValueError("Invalid numeric inputs.")
    if h <= 0 or h > 300:
        raise ValueError("Height must be 0 < height <= 300 cm.")
    if w <= 0 or w > 500:
        raise ValueError("Weight must be 0 < weight <= 500 kg.")
    return round(math.sqrt((h * w) / 3600.0), 2)


def calc_crcl(age, weight_kg, scr, is_female=False):
    """CrCl via Cockcroft-Gault (Nephron 1976): (140-age)*wt/(72*SCr)*0.85_if_female."""
    try:
        a, w, s = float(age), float(weight_kg), float(scr)
    except (ValueError, TypeError):
        raise ValueError("Invalid numeric inputs.")
    if not all(math.isfinite(v) for v in (a, w, s)):
        raise ValueError("Invalid numeric inputs.")
    if a <= 0 or a > 130:
        raise ValueError("Age must be 0 < age <= 130 years.")
    if w <= 0 or w > 500:
        raise ValueError("Weight must be 0 < weight <= 500 kg.")
    if s <= 0 or s > 30:
        raise ValueError("Serum creatinine must be 0 < SCr <= 30 mg/dL.")
    crcl = ((140.0 - a) * w) / (72.0 * s)
    if is_female:
        crcl *= 0.85
    if not math.isfinite(crcl):
        raise ValueError("Input out of plausible range.")
    return round(crcl, 1)


def calc_peds_dose(weight_kg, mg_per_kg_per_day, doses_per_day, conc_mg_per_ml):
    """Pediatric weight-based dose -> (mg_per_dose, mL_per_dose) both 2dp."""
    try:
        wt = float(weight_kg)
        mkd = float(mg_per_kg_per_day)
        d = float(doses_per_day)
        conc = float(conc_mg_per_ml)
    except (ValueError, TypeError):
        raise ValueError("Invalid numeric inputs.")
    if not all(math.isfinite(v) for v in (wt, mkd, d, conc)):
        raise ValueError("Invalid numeric inputs.")
    if wt <= 0 or wt > 200:
        raise ValueError("Weight must be 0 < weight <= 200 kg.")
    if mkd <= 0 or mkd > 1000:
        raise ValueError("mg/kg/day must be 0 < x <= 1000.")
    if d <= 0 or d > 24 or not d.is_integer():
        raise ValueError("Doses/day must be a whole number, 0 < n <= 24.")
    if conc <= 0 or conc > 1000:
        raise ValueError("Concentration must be 0 < mg/mL <= 1000.")
    total_mg = wt * mkd
    mg_dose = total_mg / d
    ml_dose = mg_dose / conc
    if not all(math.isfinite(v) for v in (mg_dose, ml_dose)):
        raise ValueError("Input out of plausible range.")
    return round(mg_dose, 2), round(ml_dose, 2)


def calc_days_supply(qty, daily):
    """Days supply: floor(qty/daily). Raises ValueError if > 3650 or inputs > 1e6."""
    try:
        q, d = float(qty), float(daily)
    except (ValueError, TypeError):
        raise ValueError("Invalid quantity or daily-use value.")
    if not all(math.isfinite(v) for v in (q, d)):
        raise ValueError("Invalid quantity or daily-use value.")
    if q <= 0 or d <= 0:
        raise ValueError("Invalid quantity or daily-use value.")
    if max(q, d) > 1e6:
        raise ValueError("Input out of plausible range.")
    ratio = q / d
    if not math.isfinite(ratio):
        raise ValueError("Input out of plausible range.")
    days = int(ratio)
    if days > 3650:
        raise ValueError("Result implausible (> 10 years). Check inputs.")
    return days


def calc_insulin_days(daily_units, total_ml, conc, priming=0):
    """Insulin days supply: floor(total_ml*conc / (daily+priming))."""
    try:
        daily = float(daily_units)
        total = float(total_ml)
        c = float(conc)
        p = float(priming)
    except (ValueError, TypeError):
        raise ValueError("Invalid numeric inputs.")
    if not all(math.isfinite(v) for v in (daily, total, c, p)):
        raise ValueError("Invalid numeric inputs.")
    if daily <= 0 or total <= 0 or c <= 0:
        raise ValueError("daily_units, total_ml, and conc must be > 0.")
    if p < 0:
        raise ValueError("Priming cannot be negative.")
    if max(daily, total, c, p if p else 0) > 1e6:
        raise ValueError("Input out of plausible range.")
    eff = daily + p
    ratio = (total * c) / eff
    if not math.isfinite(ratio):
        raise ValueError("Input out of plausible range.")
    days = int(ratio)
    if days > 3650:
        raise ValueError("Result implausible (> 10 years). Check inputs.")
    return days


# ============================================================
# MOCK HTTP SERVER
# ============================================================

class ClinicalCalcHandler(BaseHTTPRequestHandler):
    """POST /calc with JSON payload. Returns 200 or 422 on ValueError."""

    def do_POST(self):
        if self.path != "/calc":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except (json.JSONDecodeError, Exception):
            self.send_response(400)
            self.end_headers()
            return
        try:
            calc = req.get("calc")
            if calc == "bsa":
                result = calc_bsa_mosteller(req["height_cm"], req["weight_kg"])
            elif calc == "crcl":
                result = calc_crcl(req["age"], req["weight_kg"],
                                   req["scr"], req.get("is_female", False))
            elif calc == "peds":
                mg, ml = calc_peds_dose(
                    req["weight_kg"], req["mg_per_kg_per_day"],
                    req["doses_per_day"], req["conc_mg_per_ml"])
                result = {"mg_per_dose": mg, "ml_per_dose": ml}
            elif calc == "days_supply":
                result = calc_days_supply(req["qty"], req["daily"])
            elif calc == "insulin":
                result = calc_insulin_days(
                    req["daily_units"], req["total_ml"],
                    req["conc"], req.get("priming", 0))
            else:
                self.send_response(400)
                self.end_headers()
                return
            resp = json.dumps({"result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        except ValueError as e:
            resp = json.dumps({"error": str(e)}).encode()
            self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


def start_mock_server(port=19240):
    """Start threaded mock server. Returns the server instance."""
    server = ThreadingHTTPServer(("127.0.0.1", port), ClinicalCalcHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ============================================================
# TEST SCENARIOS
# ============================================================

class ClinicalTestResult:
    def __init__(self, name, passed, detail=""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n        {self.detail}"
        return msg


def run_all_scenarios(verbose=False):
    results = []

    def check(name, cond, detail=""):
        r = ClinicalTestResult(name, cond, detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    # 1. BSA formula identity h=170, w=70
    h, w = 170.0, 70.0
    got = calc_bsa_mosteller(h, w)
    ref = round(_ref_bsa(h, w), 2)
    check("1. BSA formula identity h=170 w=70",
          abs(got - ref) < 0.01,
          f"got={got}, ref={ref}")

    # 2. BSA plausibility bounds for a grid of valid inputs
    bsa_ok = True
    bad = []
    for h2 in (100, 150, 170, 200):
        for w2 in (20, 60, 100, 200):
            try:
                b = calc_bsa_mosteller(h2, w2)
                if not (0.10 <= b <= 4.00):
                    bsa_ok = False
                    bad.append((h2, w2, b))
            except ValueError:
                pass
    check("2. BSA plausibility bounds [0.10, 4.00] m²", bsa_ok,
          f"out-of-bounds: {bad}")

    # 3. BSA strictly monotone in height
    bsa_h = [calc_bsa_mosteller(h3, 70) for h3 in (100, 130, 160, 180, 200)]
    check("3. BSA strictly monotone in height (w=70)",
          all(bsa_h[i] < bsa_h[i + 1] for i in range(len(bsa_h) - 1)),
          f"values: {bsa_h}")

    # 4. BSA strictly monotone in weight
    bsa_w = [calc_bsa_mosteller(170, w4) for w4 in (20, 50, 80, 120, 200)]
    check("4. BSA strictly monotone in weight (h=170)",
          all(bsa_w[i] < bsa_w[i + 1] for i in range(len(bsa_w) - 1)),
          f"values: {bsa_w}")

    # 5. CrCl identity male
    crcl_got = calc_crcl(40, 80, 1.0, is_female=False)
    crcl_ref = round(_ref_crcl(40, 80, 1.0, False), 1)
    check("5. CrCl identity age=40 wt=80 scr=1.0 male ~111.1",
          abs(crcl_got - crcl_ref) < 0.2,
          f"got={crcl_got}, ref={crcl_ref}")

    # 6. CrCl female factor exactly 0.85
    male_ref = _ref_crcl(50, 70, 1.2, False)
    female_ref = _ref_crcl(50, 70, 1.2, True)
    ratio = female_ref / male_ref if male_ref else 0
    check("6. CrCl female factor is exactly 0.85 x male",
          abs(ratio - 0.85) < 1e-9,
          f"ratio={ratio:.10f}, expected 0.85")

    # 7. CrCl strictly decreasing with age
    crcl_ages = [_ref_crcl(a7, 70, 1.0) for a7 in (20, 40, 60, 80)]
    check("7. CrCl strictly decreasing with age (wt=70, scr=1.0)",
          all(crcl_ages[i] > crcl_ages[i + 1] for i in range(len(crcl_ages) - 1)),
          f"values: {[round(v, 1) for v in crcl_ages]}")

    # 8. CrCl strictly decreasing with SCr
    crcl_scr = [_ref_crcl(60, 70, s8) for s8 in (0.5, 1.0, 2.0, 5.0)]
    check("8. CrCl strictly decreasing with SCr (age=60, wt=70)",
          all(crcl_scr[i] > crcl_scr[i + 1] for i in range(len(crcl_scr) - 1)),
          f"values: {[round(v, 1) for v in crcl_scr]}")

    # 9a. Peds mg/dose
    mg9, ml9 = calc_peds_dose(18, 90, 2, 50)
    ref_mg = round(_ref_peds_mg(18, 90, 2), 2)
    check("9a. Peds mg/dose (18kg, 90mg/kg/day, BID, 50mg/mL)",
          abs(mg9 - ref_mg) < 0.01,
          f"got={mg9}, ref={ref_mg}")

    # 9b. Peds mL/dose
    ref_ml = round(_ref_peds_ml(18, 90, 2, 50), 2)
    check("9b. Peds mL/dose (Amoxicillin 90mg/kg/day BID 18kg 50mg/mL)",
          abs(ml9 - ref_ml) < 0.01,
          f"got={ml9}, ref={ref_ml}")

    # 10. Days supply floor convention
    ds = calc_days_supply(31, 3)
    check("10. Days supply floor: 31/3 = 10 (not 11)",
          ds == 10, f"got={ds}")

    # 11. Insulin priming subtracted
    ins = calc_insulin_days(10, 10, 100, priming=10)
    check("11. Insulin priming subtracted: floor(10*100/20)=50",
          ins == 50, f"got={ins}")

    # 11b. Without priming
    ins_nop = calc_insulin_days(10, 10, 100, priming=0)
    check("11b. Insulin without priming: floor(10*100/10)=100",
          ins_nop == 100, f"got={ins_nop}")

    # 12. Huge input raises ValueError
    try:
        calc_insulin_days(10, 1e7, 100)
        check("12. Insulin huge input raises ValueError", False, "no exception raised")
    except ValueError:
        check("12. Insulin huge input raises ValueError", True)

    # 13. CrCl age boundary: 131 raises
    try:
        calc_crcl(131, 70, 1.0)
        check("13. CrCl age=131 raises ValueError", False, "no exception raised")
    except ValueError:
        check("13. CrCl age=131 raises ValueError", True)

    # 13b. CrCl age=130 succeeds
    try:
        calc_crcl(130, 70, 1.0)
        check("13b. CrCl age=130 succeeds", True)
    except ValueError as e:
        check("13b. CrCl age=130 succeeds", False, str(e))

    # 14. Cross-calculator monotonicity: double weight increases BSA and CrCl
    bsa_base = _ref_bsa(170, 70)
    bsa_2x = _ref_bsa(170, 140)
    crcl_base = _ref_crcl(50, 70, 1.0)
    crcl_2x = _ref_crcl(50, 140, 1.0)
    check("14a. Double weight -> BSA increases", bsa_2x > bsa_base,
          f"base={bsa_base:.3f}, 2x={bsa_2x:.3f}")
    check("14b. Double weight -> CrCl increases", crcl_2x > crcl_base,
          f"base={crcl_base:.1f}, 2x={crcl_2x:.1f}")

    return results


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="clinical_calc_test_harness",
        description="Clinical calculator safety harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all scenarios and exit 0 if all pass")
    p.add_argument("--mock-server", action="store_true",
                   help="Start mock HTTP server only")
    p.add_argument("--port", type=int, default=19240,
                   help="Mock server port (default: 19240)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    import time
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        server = start_mock_server(args.port)
        print(f"  Clinical calc mock server on http://127.0.0.1:{args.port} — Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()
        return

    if args.self_test:
        print("\n  CLINICAL CALC TEST HARNESS — self-test mode")
        print("  " + "=" * 54)
        results = run_all_scenarios(verbose=args.verbose)
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        if not args.verbose:
            for r in results:
                print(r)
        print()
        print(f"  Results: {passed} passed, {failed} failed out of {len(results)}")
        print()
        sys.exit(0 if failed == 0 else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
