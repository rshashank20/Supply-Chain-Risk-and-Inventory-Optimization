#!/usr/bin/env python3
"""
Risk Management Engine — Risk-Aware Safety-Stock Optimization and
Material Shortage Decision Support System.

Implements the risk-assessment half of the brief's risk methodology:
an ISO 31000-style lifecycle of (1) risk identification, (2) risk analysis
(probability-impact scoring), (3) risk evaluation (priority classes) and
(4) risk treatment (mitigation recommendations with cost basis and owner).

ALL DATA IS SYNTHETIC (seeded generator). Nothing in the output may be
presented as real company data.

What this module does
---------------------
1. Reads supplier/material performance features straight from the SQLite
   database (no invented inputs; every number traces to a query below).
2. Scores each active (material, supplier) pair on ordinal 1-5 likelihood
   and 1-5 impact scales. Priority = likelihood x impact, bucketed into
   Low (1-4), Moderate (5-9), High (10-16), Critical (17-25). These are
   PROJECT-DEFINED ORDINAL CUTOFFS, not calibrated probabilities.
3. Computes expected loss in INR only where empirically defensible:
   an observed disruption frequency from receipt-level lateness/rejection
   history (n >= 20 receipts) times an average event loss taken from the
   risk_events table (n >= 5 events in the loss-basis category).
   RULE: an ordinal 1-5 rating is NEVER multiplied by currency. Expected
   loss is only reported where the frequency is empirical.
4. Recommends one treatment per High/Critical risk from a fixed playbook
   (expedite_po, alternate_source, safety_stock_raise, reprioritize_orders,
   quality_audit), each with quoted triggering evidence, an estimated cost
   basis and an owning role. An alternate source is only ever proposed if
   that supplier is qualified for the material in supplier_material.
5. Writes data/processed/risk_scores.csv and prints a console summary.

Run:  python3 src/risk_management/risk_engine.py
"""

from __future__ import annotations

import csv
import math
import random
import sqlite3
import statistics
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------
# File layout: <root>/src/risk_management/risk_engine.py
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "processed" / "shortage_dss.db"
CONFIG_PATH = ROOT / "config" / "project_config.yaml"
OUT_PATH = ROOT / "data" / "processed" / "risk_scores.csv"


def load_risk_config() -> dict:
    """Read risk parameters from project_config.yaml; documented defaults
    apply for any key not present (constants below the fallback)."""
    defaults = {
        "lookback_days": 90,
        "min_receipts_expected_loss": 20,
        "min_loss_events": 5,
        "otd_bands": [98, 95, 90, 80],
        "lateness_bands": [0.5, 2, 5, 10],
        "reject_bands": [0.005, 0.01, 0.025, 0.05],
        "cover_bands": [60, 30, 15, 7],
        "expedite_premium_rate": 0.05,
        "quality_audit_cost_inr": 50000,
        "ss_raise_fraction": 0.5,
        "shortage_flag_horizon_days": 30,
    }
    try:
        with open(CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh) or {}
        defaults.update(cfg.get("risk_management", {}) or {})
    except FileNotFoundError:
        pass  # run with documented defaults; note it in the console output
    return defaults


CFG = load_risk_config()

# ---------------------------------------------------------------------------
# Scale definitions (documented per the brief). Ordinal 1-5 scales; the
# priority cutoffs below are project-defined, NOT calibrated probabilities.
# ---------------------------------------------------------------------------
# LIKELIHOOD (supplier reliability, per material-supplier pair):
#   component bands combine OTD %, average lateness and rejection rate:
#   OTD >=98% ->1 | >=95% ->2 | >=90% ->3 | >=80% ->4 | <80% ->5
#   avg lateness <0.5d ->1 | <2d ->2 | <5d ->3 | <10d ->4 | >=10d ->5
#   reject rate  <0.5% ->1 | <1% ->2 | <2.5% ->3 | <5% ->4 | >=5% ->5
#   likelihood = round(0.5*otd + 0.3*lateness + 0.2*rejection), clipped 1..5.
#   (Delivery performance dominates because supply interruption is the risk.)
# IMPACT (material consequence if supply is disrupted):
#   criticality A=3 | B=2 | C=1   (A = stops production)
#   days of cover >=60d ->1 | >=30d ->2 | >=15d ->3 | >=7d ->4 | <7d ->5
#   annual spend quintile 1..5 (top quintile = 5: more money at risk)
#   impact = round(0.45*criticality + 0.35*cover + 0.20*spend), +1 if the
#   material has a single qualified source (single point of failure),
#   clipped to 1..5.
# PRIORITY = likelihood x impact -> Low 1-4, Moderate 5-9, High 10-16,
# Critical 17-25.
PRIORITY_CLASSES = [(17, "Critical"), (10, "High"), (5, "Moderate"), (1, "Low")]

# Weights are project-defined ordinal combination rules (documented above).
L_W = {"otd": 0.5, "lateness": 0.3, "rejection": 0.2}
I_W = {"criticality": 0.45, "cover": 0.35, "spend": 0.20}

# Holding-cost rate for safety-stock cost estimates: read from
# config safety_stock.holding_cost_rate_annual where present, else 0.22.
_HOLDING_RATE_DEFAULT = 0.22

_FULL_CFG: dict = {}
try:
    with open(CONFIG_PATH) as _fh:
        _FULL_CFG = yaml.safe_load(_fh) or {}
except FileNotFoundError:
    pass


def holding_rate() -> float:
    """Annual holding-cost rate as a fraction of unit value."""
    return float((_FULL_CFG.get("safety_stock") or {})
                 .get("holding_cost_rate_annual", _HOLDING_RATE_DEFAULT))

CRIT_SCORE = {"A": 3, "B": 2, "C": 1}

# Fixed treatment playbook (brief requirement).
ACTIONS = ("expedite_po", "alternate_source", "safety_stock_raise",
           "reprioritize_orders", "quality_audit", "monitor")
OWNER_ROLE = {
    "expedite_po": "Procurement Manager",
    "alternate_source": "Sourcing Manager",
    "safety_stock_raise": "Materials Planner",
    "reprioritize_orders": "Production Planner",
    "quality_audit": "Supplier Quality Engineer",
    "monitor": "Materials Planner",
}

# Which risk_events.event_type rows may be used as the loss-magnitude basis
# for each risk category. The frequency always comes from observed receipt
# history of the specific (material, supplier) pair.
LOSS_BASIS = {
    "supplier_delivery": ("late_delivery", "partial_shipment"),
    "quality": ("quality_hold",),
    "single_source": ("late_delivery", "partial_shipment", "quality_hold",
                      "demand_spike"),
}


def clip1_5(x: float) -> int:
    """Clip a score to the 1-5 ordinal range and round to an integer."""
    return max(1, min(5, int(round(x))))


def band(value: float, cutoffs: list, high_good: bool) -> int:
    """Map a continuous value to an ordinal 1..5 via descending cutoffs.

    high_good=True (e.g. OTD%): value >= cutoffs[0] -> 1 ... below last -> 5.
    high_good=False (e.g. lateness): value < cutoffs[0] -> 1 ... above -> 5.
    """
    score = 1
    for i, c in enumerate(cutoffs):
        if high_good:
            if value < c:
                score = i + 2
        else:
            if value >= c:
                score = i + 2
    return max(1, min(5, score))


def priority_class(score: int) -> str:
    """Bucket likelihood x impact into the project-defined priority classes."""
    for cutoff, name in PRIORITY_CLASSES:
        if score >= cutoff:
            return name
    return "Low"  # unreachable; kept for safety


# ---------------------------------------------------------------------------
# Feature extraction — every number comes from the database
# ---------------------------------------------------------------------------
def get_anchor_date(con: sqlite3.Connection) -> str:
    """Anchor the 'recent' lookback window on the latest observed date."""
    row = con.execute(
        "SELECT MAX(d) FROM (SELECT MAX(receipt_date) AS d FROM goods_receipts "
        "UNION ALL SELECT MAX(transaction_date) FROM inventory_transactions)"
    ).fetchone()
    return row[0]


def fetch_supplier_features(con: sqlite3.Connection, since: str) -> dict:
    """Per (material, supplier) receipt-history features over the lookback
    window. Returned dict keyed by (material_id, supplier_id).

    OTD = receipts with receipt_date <= promised_date (a receipt early or on
    time counts as on time). Lateness counts only positive delay. Lead time =
    receipt_date - order_date (actual observed lead time). Rejection rate =
    rejected_qty / (received_qty + rejected_qty). Volume = received qty.
    """
    rows = con.execute(
        """
        SELECT l.material_id, h.supplier_id,
               g.receipt_date, l.promised_date, h.order_date,
               g.received_qty, g.rejected_qty
        FROM goods_receipts g
        JOIN purchase_order_lines l ON g.po_line_id = l.po_line_id
        JOIN purchase_orders h ON l.po_id = h.po_id
        WHERE g.receipt_date >= ?
        """,
        (since,),
    ).fetchall()

    feats: dict = {}
    for mat, sup, rcpt, prom, ordered, recv, rej in rows:
        key = (mat, sup)
        f = feats.setdefault(key, {
            "n_receipts": 0, "n_on_time": 0, "late_days": [],
            "lead_times": [], "recv_qty": 0.0, "rej_qty": 0.0})
        f["n_receipts"] += 1
        late = max(0.0, _julian(rcpt) - _julian(prom))
        f["late_days"].append(late)
        if late == 0:
            f["n_on_time"] += 1
        f["lead_times"].append(max(0.0, _julian(rcpt) - _julian(ordered)))
        f["recv_qty"] += recv or 0.0
        f["rej_qty"] += rej or 0.0

    for f in feats.values():
        n = f["n_receipts"]
        f["otd_pct"] = 100.0 * f["n_on_time"] / n
        f["avg_lateness_days"] = sum(f["late_days"]) / n
        f["lt_mean"] = statistics.fmean(f["lead_times"])
        f["lt_sd"] = statistics.pstdev(f["lead_times"]) if n > 1 else 0.0
        denom = f["recv_qty"] + f["rej_qty"]
        f["rejection_rate"] = f["rej_qty"] / denom if denom else 0.0
    return feats


def fetch_full_history_counts(con: sqlite3.Connection) -> dict:
    """Per (material, supplier): full-history receipt counts and disruption
    flags (late OR any rejected qty), used for the empirical expected-loss
    frequency. Sample-size gate is applied later."""
    rows = con.execute(
        """
        SELECT l.material_id, h.supplier_id,
               COUNT(*) AS n,
               SUM(CASE WHEN g.receipt_date > l.promised_date
                          OR g.rejected_qty > 0 THEN 1 ELSE 0 END) AS n_disrupted
        FROM goods_receipts g
        JOIN purchase_order_lines l ON g.po_line_id = l.po_line_id
        JOIN purchase_orders h ON l.po_id = h.po_id
        GROUP BY 1, 2
        """
    ).fetchall()
    return {(m, s): {"n_receipts_total": n, "n_disrupted": d or 0}
            for m, s, n, d in rows}


def fetch_material_features(con: sqlite3.Connection, since: str,
                            horizon_days: int,
                            history_years: float) -> dict:
    """Per-material features:
      - current stock = sum of signed_qty (ISSUE is stored negative)
      - avg daily demand = ISSUE volume over the lookback / lookback days
      - days of cover = stock / avg daily demand (999 when no demand)
      - shortage flag: projected stock after `horizon_days` of demand <
        the material's safety stock (basic requirements proxy: trailing
        average daily demand x horizon)
      - single_source flag: only one qualified supplier in supplier_material
      - annual spend: receipt value (qty x PO line price) / history years
    """
    days = max(1, ( _julian(get_anchor_date(con)) - _julian(since) ))
    feats = {}
    for (mat, crit, ss, cost) in con.execute(
            "SELECT material_id, criticality, safety_stock, unit_cost "
            "FROM materials").fetchall():
        stock = con.execute(
            "SELECT COALESCE(SUM(signed_qty),0) FROM inventory_transactions "
            "WHERE material_id=?", (mat,)).fetchone()[0]
        issued = con.execute(
            "SELECT COALESCE(SUM(-signed_qty),0) FROM inventory_transactions "
            "WHERE material_id=? AND type='ISSUE' AND transaction_date>=?",
            (mat, since)).fetchone()[0]
        avg_daily_demand = issued / days
        cover = stock / avg_daily_demand if avg_daily_demand > 0 else 999.0
        projected = stock - avg_daily_demand * horizon_days
        n_sup = con.execute(
            "SELECT COUNT(*) FROM supplier_material WHERE material_id=?",
            (mat,)).fetchone()[0]
        spend = con.execute(
            """SELECT COALESCE(SUM(g.received_qty * l.unit_price),0)
               FROM goods_receipts g
               JOIN purchase_order_lines l ON g.po_line_id = l.po_line_id
               WHERE l.material_id = ?""", (mat,)).fetchone()[0]
        feats[mat] = {
            "criticality": crit, "safety_stock": ss or 0.0,
            "unit_cost": cost or 0.0, "current_stock": stock,
            "avg_daily_demand": avg_daily_demand,
            "days_of_cover": cover,
            "shortage_flag": projected < (ss or 0.0),
            "projected_stock": projected,
            "n_qualified_suppliers": n_sup,
            "single_source": n_sup <= 1,
            "annual_spend": spend / history_years,
        }
    return feats


def fetch_qualified_suppliers(con: sqlite3.Connection) -> dict:
    """material_id -> list of qualified supplier rows (for alternate-source
    eligibility: an alternate is only ever a supplier present here)."""
    out: dict = {}
    for row in con.execute(
            "SELECT material_id, supplier_id, lead_time_days, unit_price "
            "FROM supplier_material"):
        out.setdefault(row[0], []).append({
            "supplier_id": row[1], "lead_time_days": row[2],
            "unit_price": row[3]})
    return out


def fetch_loss_basis(con: sqlite3.Connection) -> dict:
    """Average estimated_loss per risk_events.event_type (the magnitude leg
    of expected loss; the frequency leg is pair-specific and empirical)."""
    out = {}
    for et, n, avg_loss in con.execute(
            "SELECT event_type, COUNT(*), AVG(estimated_loss) "
            "FROM risk_events GROUP BY event_type"):
        out[et] = {"n": n, "avg_loss": avg_loss or 0.0}
    return out


def _julian(d: str) -> float:
    """Ordinal day number for YYYY-MM-DD strings (avoids sqlite round-trips
    when computing lateness/lead times in Python)."""
    y, m, dd = (int(p) for p in d.split("-"))
    import datetime as dt
    return dt.date(y, m, dd).toordinal()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_likelihood(f: dict) -> tuple[int, dict]:
    """Ordinal 1-5 likelihood from observed supplier performance."""
    parts = {
        "otd": band(f["otd_pct"], CFG["otd_bands"], high_good=True),
        "lateness": band(f["avg_lateness_days"], CFG["lateness_bands"],
                         high_good=False),
        "rejection": band(f["rejection_rate"], CFG["reject_bands"],
                          high_good=False),
    }
    score = clip1_5(sum(parts[k] * L_W[k] for k in parts))
    return score, parts


def score_impact(m: dict, spend_score: int) -> tuple[int, dict]:
    """Ordinal 1-5 impact from material consequence factors."""
    parts = {
        "criticality": CRIT_SCORE.get(m["criticality"], 2),
        "cover": band(m["days_of_cover"], CFG["cover_bands"], high_good=True),
        "spend": spend_score,
    }
    score = clip1_5(sum(parts[k] * I_W[k] for k in parts))
    if m["single_source"] and score < 5:
        score += 1  # single point of failure amplifies consequence
        parts["single_source_bump"] = 1
    return score, parts


def classify_category(m: dict, f: dict) -> str:
    """Dominant risk category for the pair (drives treatment selection):
    structural single-sourcing first, then quality, else delivery."""
    if m["single_source"]:
        return "single_source"
    if f["rejection_rate"] >= CFG["reject_bands"][2]:  # >=2.5% rejection
        return "quality"
    return "supplier_delivery"


def expected_loss(f_hist: dict, category: str, annual_receipts: float,
                  loss_basis: dict) -> tuple[str, str]:
    """Expected annual loss in INR — computed ONLY where defensible:
       E[loss] = empirical disruption frequency x annual receipts x
       average event loss (from risk_events of the matching category).
    A receipt counts as disrupted if late OR any qty rejected.
    Returns (value_string, method_note); value_string is '' (blank) when
    the sample-size gates fail. The ordinal 1-5 scores are never involved
    in this multiplication (project rule).
    """
    n = f_hist["n_receipts_total"]
    if n < CFG["min_receipts_expected_loss"]:
        return "", (f"insufficient history (n={n} receipts < "
                    f"{CFG['min_receipts_expected_loss']})")
    events = [loss_basis[e] for e in LOSS_BASIS[category] if e in loss_basis]
    n_ev = sum(e["n"] for e in events)
    if n_ev < CFG["min_loss_events"]:
        return "", (f"insufficient loss basis (n={n_ev} events < "
                    f"{CFG['min_loss_events']})")
    freq = f_hist["n_disrupted"] / n  # empirical, from observed receipts
    avg_loss = (sum(e["n"] * e["avg_loss"] for e in events) / n_ev)
    value = freq * annual_receipts * avg_loss
    note = (f"E = freq({f_hist['n_disrupted']}/{n} disrupted receipts) x "
            f"{annual_receipts:.1f} receipts/yr x avg loss INR "
            f"{avg_loss:,.0f} over {n_ev} {category} risk_events")
    return f"{value:,.0f}", note


# ---------------------------------------------------------------------------
# Treatment selection
# ---------------------------------------------------------------------------
def pick_alternate(material_id: str, current_supplier: str,
                   qualified: list, sup_feats: dict) -> dict | None:
    """Best qualified alternate: a different supplier for the same material
    whose recent OTD is acceptable. Returns None if none qualifies."""
    best = None
    for q in qualified:
        if q["supplier_id"] == current_supplier:
            continue
        f = None
        for (m, s), feat in sup_feats.items():
            if m == material_id and s == q["supplier_id"]:
                f = feat
                break
        if f is None or f["otd_pct"] < CFG["otd_bands"][1]:  # need >=95% OTD
            continue
        if best is None or f["otd_pct"] > best[1]["otd_pct"]:
            best = (q, f)
    return best


def recommend(material_id: str, supplier_id: str, category: str,
              pclass: str, f: dict, m: dict, qualified: list,
              sup_feats: dict) -> tuple[str, str, str]:
    """Choose one treatment for a High/Critical risk (monitor for the rest).
    Returns (action, cost_basis, evidence). Cost bases are stated estimates;
    assumptions live in the config and the notes below.
    """
    ss = m["safety_stock"]
    cover = m["days_of_cover"]
    otd = f["otd_pct"]
    alt = pick_alternate(material_id, supplier_id, qualified, sup_feats)

    if category == "quality":
        return ("quality_audit",
                f"INR {CFG['quality_audit_cost_inr']:,} fixed engagement "
                f"(config quality_audit_cost_inr)",
                f"rejection rate {f['rejection_rate']:.1%} over last "
                f"{CFG['lookback_days']}d ({f['n_receipts']} receipts); "
                f"avg unit price INR {m['unit_cost']:,.0f}")

    if category == "single_source":
        if alt:
            q, af = alt
            saving = (f.get("unit_price", 0) - q["unit_price"]) \
                * f["recv_qty"] * (365.25 / CFG["lookback_days"])
            return ("alternate_source",
                    f"dual-sourcing with {q['supplier_id']} "
                    f"(INR {q['unit_price']:,.0f}/unit vs current "
                    f"INR {f.get('unit_price', 0):,.0f}/unit; "
                    f"{'saves' if saving >= 0 else 'costs'} approx INR "
                    f"{abs(saving):,.0f}/yr on recent volume)",
                    f"single qualified source for {material_id}; "
                    f"{q['supplier_id']} qualified (OTD {af['otd_pct']:.0f}% "
                    f"over last {CFG['lookback_days']}d)")
        raise_cost = (ss * CFG["ss_raise_fraction"] * m["unit_cost"]
                      * holding_rate())
        return ("safety_stock_raise",
                f"raising SS {CFG['ss_raise_fraction']:.0%} adds ~INR "
                f"{raise_cost:,.0f}/yr holding cost "
                f"({holding_rate():.0%} of unit value, config safety_stock)",
                f"single qualified source for {material_id}; no qualified "
                f"alternate with acceptable OTD; {cover:.0f}d cover")

    # category == "supplier_delivery"
    if otd < 80 and cover < 30:
        shipment_value = f["recv_qty"] / max(1, f["n_receipts"]) \
            * f.get("unit_price", m["unit_cost"])
        premium = shipment_value * CFG["expedite_premium_rate"]
        return ("expedite_po",
                f"{CFG['expedite_premium_rate']:.0%} expedite premium on next "
                f"shipment ~INR {premium:,.0f} "
                f"(avg shipment value INR {shipment_value:,.0f})",
                f"OTD {otd:.0f}% over last {CFG['lookback_days']}d "
                f"({f['n_receipts'] - f['n_on_time']}/{f['n_receipts']} "
                f"receipts late, avg lateness "
                f"{f['avg_lateness_days']:.1f}d); only {cover:.0f}d cover")
    if alt:
        q, af = alt
        return ("alternate_source",
                f"shift volume to {q['supplier_id']} at INR "
                f"{q['unit_price']:,.0f}/unit "
                f"(current INR {f.get('unit_price', 0):,.0f}/unit)",
                f"OTD {otd:.0f}% over last {CFG['lookback_days']}d vs "
                f"{q['supplier_id']} OTD {af['otd_pct']:.0f}% "
                f"(qualified for {material_id})")
    if m["shortage_flag"] or cover < 30:
        raise_cost = (ss * CFG["ss_raise_fraction"] * m["unit_cost"]
                      * holding_rate())
        flag = ("open shortage flag: projected stock "
                f"{m['projected_stock']:,.0f} < safety stock {ss:,.0f}; "
                ) if m["shortage_flag"] else ""
        return ("safety_stock_raise",
                f"raising SS {CFG['ss_raise_fraction']:.0%} adds ~INR "
                f"{raise_cost:,.0f}/yr holding cost "
                f"({holding_rate():.0%} of unit value, config safety_stock)",
                f"{flag}OTD {otd:.0f}% over last {CFG['lookback_days']}d; "
                f"{cover:.0f}d cover; no qualified alternate with "
                f"acceptable OTD")
    return ("reprioritize_orders",
            "nominal — internal replanning effort, no direct spend",
            f"OTD {otd:.0f}% over last {CFG['lookback_days']}d, "
            f"{cover:.0f}d cover, no shortage flag; absorb via "
            f"production rescheduling")


def build_evidence(f: dict, m: dict, category: str) -> str:
    """One-line quoted evidence for the CSV (also reused in the console)."""
    bits = [
        f"OTD {f['otd_pct']:.0f}% over last {CFG['lookback_days']}d "
        f"({f['n_receipts']} receipts, avg lateness "
        f"{f['avg_lateness_days']:.1f}d)",
        f"rejection {f['rejection_rate']:.1%}",
    ]
    if category == "single_source":
        bits.append("single qualified source")
    bits.append(f"{m['days_of_cover']:.0f}d cover vs SS {m['safety_stock']:,.0f}")
    if m["shortage_flag"]:
        bits.append("open shortage flag "
                    f"(proj {m['projected_stock']:,.0f} < SS)")
    return "; ".join(bits)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run() -> list[dict]:
    """Compute risk scores for every active (material, supplier) pair."""
    random.seed(42)  # deterministic; no randomness is used in scoring
    if not DB_PATH.exists():
        sys.exit(f"database not found: {DB_PATH}")

    con = sqlite3.connect(DB_PATH)
    anchor = get_anchor_date(con)
    since = _add_days(anchor, -CFG["lookback_days"])
    horizon = CFG["shortage_flag_horizon_days"]

    sup_feats = fetch_supplier_features(con, since)
    hist = fetch_full_history_counts(con)
    hist_years = _history_years(con)
    mat_feats = fetch_material_features(con, since, horizon, hist_years)
    qualified = fetch_qualified_suppliers(con)
    loss_basis = fetch_loss_basis(con)

    # Spend quintile across materials (top quintile = highest impact score).
    spends = sorted(m["annual_spend"] for m in mat_feats.values())
    def spend_score(v: float) -> int:
        if v <= 0:
            return 1
        rank = sum(1 for s in spends if s <= v) / len(spends)
        return clip1_5(math.ceil(rank * 5))

    # Disruption exposure per material: share of recent volume from
    # suppliers with recent lateness (avg lateness > 2d).
    exposure: dict = {}
    for (mat, _sup), f in sup_feats.items():
        e = exposure.setdefault(mat, {"late_vol": 0.0, "tot_vol": 0.0})
        e["tot_vol"] += f["recv_qty"]
        if f["avg_lateness_days"] > CFG["lateness_bands"][1]:
            e["late_vol"] += f["recv_qty"]

    rows = []
    for (mat, sup), f in sorted(sup_feats.items()):
        m = mat_feats[mat]
        # Attach current unit price for cost-basis math.
        f = dict(f)
        qrow = next((q for q in qualified.get(mat, [])
                     if q["supplier_id"] == sup), None)
        f["unit_price"] = qrow["unit_price"] if qrow else m["unit_cost"]

        likelihood, l_parts = score_likelihood(f)
        impact, i_parts = score_impact(m, spend_score(m["annual_spend"]))
        priority = likelihood * impact
        pclass = priority_class(priority)
        category = classify_category(m, f)

        exp = exposure.get(mat, {"late_vol": 0.0, "tot_vol": 0.0})
        exp_share = (exp["late_vol"] / exp["tot_vol"]) if exp["tot_vol"] else 0.0

        annual_receipts = (hist[(mat, sup)]["n_receipts_total"]
                           / hist_years)
        el_value, el_note = expected_loss(hist[(mat, sup)], category,
                                          annual_receipts, loss_basis)

        if pclass in ("High", "Critical"):
            action, cost_basis, evidence = recommend(
                mat, sup, category, pclass, f, m,
                qualified.get(mat, []), sup_feats)
        else:
            action = "monitor"
            cost_basis = "routine monitoring — no incremental cost"
            evidence = build_evidence(f, m, category)

        rows.append({
            "material_id": mat,
            "supplier_id": sup,
            "risk_category": category,
            "likelihood": likelihood,
            "impact": impact,
            "priority_score": priority,
            "priority_class": pclass,
            "expected_loss_inr": el_value,
            "recommended_action": action,
            "action_cost_basis": cost_basis,
            "owner_role": OWNER_ROLE[action],
            "evidence": evidence,
            # debug/traceability (not in CSV):
            "_l_parts": l_parts, "_i_parts": i_parts,
            "_el_note": el_note,
            "_disruption_exposure": round(exp_share, 3),
            "_days_of_cover": round(m["days_of_cover"], 1),
            "_otd": round(f["otd_pct"], 1),
        })
    con.close()
    return rows


def _add_days(d: str, delta: int) -> str:
    import datetime as dt
    y, m, dd = (int(p) for p in d.split("-"))
    return (dt.date(y, m, dd) + dt.timedelta(days=delta)).isoformat()


def _history_years(con: sqlite3.Connection) -> float:
    """Span of receipt history in years (for annualising frequencies)."""
    lo, hi = con.execute(
        "SELECT MIN(receipt_date), MAX(receipt_date) FROM goods_receipts"
    ).fetchone()
    return max(1.0, (_julian(hi) - _julian(lo)) / 365.25)


def write_csv(rows: list[dict]) -> None:
    cols = ["material_id", "supplier_id", "risk_category", "likelihood",
            "impact", "priority_score", "priority_class",
            "expected_loss_inr", "recommended_action", "action_cost_basis",
            "owner_role", "evidence"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (-r["priority_score"],
                                            r["material_id"], r["supplier_id"])):
            w.writerow(r)


def print_summary(rows: list[dict]) -> None:
    print("=" * 78)
    print("RISK MANAGEMENT ENGINE — summary (SYNTHETIC DATA, seeded generator)")
    print("=" * 78)
    print("Scale definitions (project-defined ordinal cutoffs, NOT calibrated")
    print("probabilities): likelihood 1-5 (supplier reliability: OTD, lateness,")
    print("rejection), impact 1-5 (criticality, days of cover, spend, single-")
    print("source bump). Priority = likelihood x impact -> Low 1-4, Moderate")
    print("5-9, High 10-16, Critical 17-25.")
    print("Expected-loss rule: ordinal 1-5 scores are NEVER multiplied by")
    print("currency. Expected loss = empirical disruption frequency (receipt")
    print("lateness/rejection history, n>=20) x annual receipts x avg event")
    print(f"loss from risk_events (n>={CFG['min_loss_events']}); blank otherwise.")
    print("-" * 78)
    counts: dict = {}
    for r in rows:
        counts[r["priority_class"]] = counts.get(r["priority_class"], 0) + 1
    print("Pairs scored:", len(rows))
    print("By priority class:",
          ", ".join(f"{k}={counts.get(k, 0)}"
                    for k in ("Critical", "High", "Moderate", "Low")))
    acts: dict = {}
    for r in rows:
        if r["priority_class"] in ("High", "Critical"):
            acts[r["recommended_action"]] = \
                acts.get(r["recommended_action"], 0) + 1
    print("Treatments (High/Critical):",
          ", ".join(f"{k}={v}" for k, v in sorted(acts.items())))
    n_el = sum(1 for r in rows if r["expected_loss_inr"])
    print(f"Rows with defensible expected loss: {n_el}")
    print("-" * 78)
    print("TOP 10 RISKS (by priority score)")
    top = sorted(rows, key=lambda r: (-r["priority_score"], r["material_id"]))
    for r in top[:10]:
        print(f"  {r['material_id']} / {r['supplier_id']} | "
              f"{r['risk_category']} | L{r['likelihood']}xI{r['impact']}="
              f"{r['priority_score']} {r['priority_class']} | "
              f"{r['recommended_action']} ({r['owner_role']})")
        print(f"      evidence: {r['evidence']}")
        if r["expected_loss_inr"]:
            print(f"      expected loss: INR {r['expected_loss_inr']}/yr")
    print("=" * 78)
    print(f"CSV written: {OUT_PATH}")


def main() -> None:
    rows = run()
    write_csv(rows)
    print_summary(rows)


if __name__ == "__main__":
    main()
