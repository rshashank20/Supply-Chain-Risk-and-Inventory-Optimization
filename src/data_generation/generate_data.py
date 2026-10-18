"""
Synthetic data generator: Manufacturing Supply Chain Risk & Material Shortage DSS
================================================================================
Generates a reproducible 24-month synthetic dataset for a tool-engineering
manufacturing context (Spectrum Tool Engineers-style).

  python3 generate_data.py                  # writes data/processed/shortage_dss.db
  python3 generate_data.py --seed 7        # different reproducible variant
  python3 generate_data.py --config config/project_config.yaml

Business rules are documented inline. Every random draw uses the seeded RNG,
so the same seed always produces the same database.

Tables (17):
  Master: products, materials, bom_lines, suppliers, supplier_material
  Planning: production_orders
  Procurement: purchase_orders, purchase_order_lines, goods_receipts
  Inventory: inventory_transactions (+ opening balances)
  Risk: risk_register, risk_events, mitigation_actions
  Policy/scenario: dim_date, inventory_policy_params, scenario_config,
                   simulation_results (populated by experiments)
"""
import argparse
import random
import sqlite3
from datetime import date, timedelta

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ----------------------------------------------------------------------------
# Configuration (defaults; overridden by config/project_config.yaml when present)
# ----------------------------------------------------------------------------
HISTORY_START = date(2024, 10, 1)
HISTORY_END = date(2026, 9, 30)          # 24 months of history

N_SUPPLIERS = 20
N_MATERIALS = 200
N_PRODUCTS = 40

# Supplier tiers drive lead-time behaviour, lateness and quality.
# (share of suppliers, lead-time mean/sd days, late-day params, defect rate)
SUPPLIER_TIERS = {
    "reliable": {"share": 0.30, "lt_mean": 14, "lt_sd": 3,  "late_p": 0.08,
                 "late_max": 3,  "defect": 0.005},
    "standard": {"share": 0.45, "lt_mean": 25, "lt_sd": 7,  "late_p": 0.25,
                 "late_max": 10, "defect": 0.02},
    "risky":    {"share": 0.25, "lt_mean": 40, "lt_sd": 14, "late_p": 0.45,
                 "late_max": 25, "defect": 0.05},
}
# One supplier suffers a 6-week disruption (lead times x3) -> shortage events.
DISRUPTION = {"supplier_idx": 6, "start": date(2025, 3, 1),
              "end": date(2025, 4, 15), "lt_multiplier": 3.0}

MATERIAL_CATEGORIES = [
    ("alloy_steel", "kg", 180), ("tool_steel", "kg", 320), ("fasteners", "pcs", 4),
    ("carbide_inserts", "pcs", 95), ("abrasives", "pcs", 22), ("polymers", "kg", 140),
    ("electronics", "pcs", 210), ("lubricants", "ltr", 90),
]
PRODUCT_FAMILIES = ["cutting_tools", "jigs_fixtures", "press_parts", "mould_bases"]

rng = random.Random(42)

# Active configuration (defaults above; load_config() overrides from YAML).
CFG = {}


def load_config(path=None):
    """Load config/project_config.yaml and override module defaults.

    Falls back to hardcoded defaults when the file or PyYAML is unavailable,
    so the generator always runs.
    """
    global HISTORY_START, HISTORY_END, N_SUPPLIERS, N_MATERIALS, N_PRODUCTS
    global SUPPLIER_TIERS, DISRUPTION, MATERIAL_CATEGORIES, CFG
    if not (path and _HAS_YAML):
        return {}
    import os
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        cfg = yaml.safe_load(f)
    CFG = cfg
    h = cfg.get("history", {})
    HISTORY_START = date.fromisoformat(h.get("start", HISTORY_START.isoformat()))
    HISTORY_END = date.fromisoformat(h.get("end", HISTORY_END.isoformat()))
    s = cfg.get("scale", {})
    N_SUPPLIERS = int(s.get("n_suppliers", N_SUPPLIERS))
    N_MATERIALS = int(s.get("n_materials", N_MATERIALS))
    N_PRODUCTS = int(s.get("n_products", N_PRODUCTS))
    st = cfg.get("supplier_tiers")
    if st:
        SUPPLIER_TIERS = {k: dict(v) for k, v in st.items()}
    d = cfg.get("disruption")
    if d:
        DISRUPTION = {"supplier_idx": int(d["supplier_index"]),
                      "start": date.fromisoformat(d["start"]),
                      "end": date.fromisoformat(d["end"]),
                      "lt_multiplier": float(d["lt_multiplier"])}
    mc = cfg.get("material_categories")
    if mc:
        MATERIAL_CATEGORIES = [(r[0], r[1], float(r[2])) for r in mc]
    return cfg


def rdate(start: date, end: date) -> date:
    """Uniform random date in [start, end]."""
    return start + timedelta(days=rng.randint(0, (end - start).days))


def month_start(d: date) -> date:
    return d.replace(day=1)


# ----------------------------------------------------------------------------
# 1. MASTER DATA
# ----------------------------------------------------------------------------
def make_suppliers():
    suppliers, tiers = [], []
    tier_names = list(SUPPLIER_TIERS)
    counts = {t: round(SUPPLIER_TIERS[t]["share"] * N_SUPPLIERS) for t in tier_names}
    counts[tier_names[0]] += N_SUPPLIERS - sum(counts.values())  # fix rounding
    i = 0
    regions = ["Karnataka", "Tamil Nadu", "Maharashtra", "Gujarat", "Delhi NCR"]
    for tier, n in counts.items():
        for _ in range(n):
            i += 1
            sid = f"SUP-{i:03d}"
            suppliers.append((sid, rng.choice(regions), "active",
                              rng.choice(["A", "B", "C"])))
            tiers.append((sid, tier))
    return suppliers, dict(tiers)


def make_materials():
    materials = []
    for i in range(1, N_MATERIALS + 1):
        cat, unit, base_cost = rng.choice(MATERIAL_CATEGORIES)
        # Lognormal-ish unit cost so a few materials dominate spend (ABC reality)
        unit_cost = round(base_cost * rng.lognormvariate(0, 0.7), 2)
        criticality = rng.choices(["A", "B", "C"], weights=[0.15, 0.30, 0.55])[0]
        safety_stock = round(rng.uniform(20, 200), 1)
        materials.append((f"MAT-{i:04d}", cat, unit, unit_cost, criticality,
                          safety_stock))
    return materials


def make_products():
    return [(f"FG-{i:03d}", rng.choice(PRODUCT_FAMILIES), "pcs", 1)
            for i in range(1, N_PRODUCTS + 1)]


def make_bom(products, materials):
    """Each product consumes 5-12 materials. Shared materials create contention."""
    lines, lid = [], 0
    mat_ids = [m[0] for m in materials]
    # A pool of common materials used across many products (realistic sharing)
    common = rng.sample(mat_ids, 40)
    for pid, _, _, _ in products:
        n = rng.randint(5, 12)
        chosen = set(rng.sample(common, min(4, n)))
        chosen |= set(rng.sample(mat_ids, n - len(chosen)))
        # sorted() for determinism: set iteration order depends on PYTHONHASHSEED
        for mid in sorted(chosen):
            lid += 1
            lines.append((lid, pid, mid, round(rng.uniform(0.5, 25), 3),
                          round(rng.uniform(0, 0.05), 3),
                          HISTORY_START.isoformat(), None))
    return lines


def make_supplier_material(materials, suppliers, tiers):
    """1-3 qualified suppliers per material; ~15% single-sourced (risk!)."""
    rows = []
    sup_ids = [s[0] for s in suppliers]
    mat_cost = {m[0]: m[3] for m in materials}
    for mid in [m[0] for m in materials]:
        n = 1 if rng.random() < 0.15 else rng.randint(2, 3)
        chosen = rng.sample(sup_ids, n)
        for j, sid in enumerate(chosen):
            t = SUPPLIER_TIERS[tiers[sid]]
            lt = max(3, round(rng.gauss(t["lt_mean"], t["lt_sd"])))
            price = round(mat_cost[mid] * rng.uniform(0.92, 1.12), 2)
            rows.append((sid, mid, lt, price, rng.choice([10, 25, 50, 100]),
                         rng.choice([500, 1000, 5000]), 1 if j == 0 else 0))
    return rows

# ----------------------------------------------------------------------------
# 2. TRANSACTIONAL DATA
# ----------------------------------------------------------------------------
def make_production_orders(products, bom):
    """~1500 production orders across 24 months, weighted by family popularity."""
    orders, oid = [], 0
    fam_w = {"cutting_tools": 0.35, "jigs_fixtures": 0.25, "press_parts": 0.25,
             "mould_bases": 0.15}
    prod_fam = {p[0]: p[1] for p in products}
    # Precompute material per product for later explosion
    for _ in range(1500):
        pid = rng.choices(products,
                          weights=[fam_w[prod_fam[p[0]]] for p in products])[0][0]
        oid += 1
        req = rdate(HISTORY_START, HISTORY_END)
        release = req - timedelta(days=rng.randint(14, 42))
        if release < HISTORY_START:
            release, req = HISTORY_START, HISTORY_START + timedelta(days=21)
        qty = round(rng.lognormvariate(4.5, 0.8))
        status = ("completed" if req < HISTORY_END - timedelta(days=30)
                  else rng.choice(["in_progress", "planned", "completed"]))
        orders.append((f"PO-{oid:05d}", pid, qty, release.isoformat(),
                       req.isoformat(),
                       rng.choices(["high", "normal", "low"],
                                   weights=[0.2, 0.6, 0.2])[0], status))
    return orders


def explode_requirements(orders, bom):
    """Gross material requirements per (material, month) from production orders.

    Applies scrap allowance: gross = qty * qty_per * (1 + scrap).
    """
    bom_by_product = {}
    for _, pid, mid, qpp, scrap, _, _ in bom:
        bom_by_product.setdefault(pid, []).append((mid, qpp, scrap))
    req = {}
    for _, pid, qty, release, req_date, _, _ in orders:
        month = month_start(date.fromisoformat(req_date))
        for mid, qpp, scrap in bom_by_product.get(pid, []):
            gross = qty * qpp * (1 + scrap)
            key = (mid, month)
            req[key] = req.get(key, 0) + gross
    return req


def make_purchase_orders(requirements, supplier_material, tiers):
    """Monthly lot-sized PO lines per material from gross requirements.

    order_date backs off from the need month by supplier lead time + buffer.
    promised_date lands just before the need month.
    """
    # Primary supplier per material
    primary = {}
    sm_by_mat = {}
    for sid, mid, lt, price, moq, cap, is_primary in supplier_material:
        sm_by_mat.setdefault(mid, []).append((sid, lt, price, moq))
        if is_primary:
            primary[mid] = (sid, lt, price, moq)
    pos, polines, poid, plid = [], [], 0, 0
    # Group PO lines into monthly PO headers per supplier
    headers = {}
    for (mid, month), gross in sorted(requirements.items()):
        if mid not in primary:
            continue
        sid, lt, price, moq = primary[mid]
        order_date = month - timedelta(days=lt + 7)
        promised = month - timedelta(days=3)
        if order_date < HISTORY_START - timedelta(days=60):
            continue  # outside sensible history window
        qty = max(moq, round(gross * rng.uniform(1.0, 1.15)))
        hkey = (sid, month)
        if hkey not in headers:
            poid += 1
            h = f"POH-{poid:05d}"
            headers[hkey] = h
            pos.append((h, sid, order_date.isoformat(), "closed", "INR"))
        plid += 1
        polines.append((f"POL-{plid:06d}", headers[hkey], mid, qty,
                        promised.isoformat(), price))
    return pos, polines


def make_receipts(polines, po_headers, supplier_material, tiers):
    """Goods receipts with tier-based lateness, partial splits and rejections.

    Rejected quantity never becomes usable stock (core business rule).
    The disruption supplier's receipts in the disruption window arrive very late.
    """
    sid_of_header = {poh: sid for poh, sid, _, _, _ in po_headers}
    tier_of_sup = {}
    for sid, mid, lt, price, moq, cap, is_primary in supplier_material:
        tier_of_sup.setdefault(sid, tiers[sid])
    dis_sid = f"SUP-{DISRUPTION['supplier_idx']:03d}"

    receipts, rid = [], 0
    for plid, poh, mid, qty, promised, price in polines:
        sid = sid_of_header[poh]
        t = SUPPLIER_TIERS[tier_of_sup[sid]]
        promised_d = date.fromisoformat(promised)

        # Lateness draw: with prob late_p, late by up to late_max days
        late_days = (rng.randint(1, t["late_max"])
                     if rng.random() < t["late_p"] else 0)
        # Disruption window: this supplier's deliveries collapse
        if (sid == dis_sid
                and DISRUPTION["start"] <= promised_d <= DISRUPTION["end"]):
            late_days += rng.randint(15, 35)

        # 20% of lines split into two partial receipts
        splits = 2 if rng.random() < 0.20 else 1
        remaining = qty
        for s in range(splits):
            rid += 1
            rq = remaining if s == splits - 1 else round(qty * rng.uniform(0.4, 0.6))
            remaining -= rq
            # Rejections scale with tier defect rate
            rejected = min(rq, round(rq * t["defect"] * rng.uniform(0.5, 2.0)))
            rec_date = promised_d + timedelta(days=late_days + s * rng.randint(1, 5))
            receipts.append((rid, plid, rec_date.isoformat(), rq, rejected))
    return receipts


def make_inventory(materials, receipts, orders, bom, line_material):
    """Inventory ledger derived from receipts (+) and production issues (-).

    Opening balance ~ 1-2 months of average consumption. Reconciliation holds
    by construction: closing = opening + sum(signed_qty).
    """
    # Consumption per material per date from production orders
    bom_by_product = {}
    for _, pid, mid, qpp, scrap, _, _ in bom:
        bom_by_product.setdefault(pid, []).append((mid, qpp, scrap))
    consumption = {}
    for _, pid, qty, release, req_date, _, status in orders:
        if status == "planned":
            continue  # not yet consumed
        d = date.fromisoformat(release)
        for mid, qpp, scrap in bom_by_product.get(pid, []):
            key = (mid, d)
            consumption[key] = consumption.get(key, 0) + qty * qpp * (1 + scrap)

    # Average monthly consumption per material -> opening balance
    monthly = {}
    for (mid, d), q in consumption.items():
        key = (mid, d.year, d.month)
        monthly[key] = monthly.get(key, 0) + q
    avg_monthly = {}
    for (mid, y, m), q in monthly.items():
        avg_monthly.setdefault(mid, []).append(q)
    avg_monthly = {mid: sum(v) / len(v) for mid, v in avg_monthly.items()}

    txns, tid = [], 0
    for mid, cat, unit, cost, crit, ss in materials:
        opening = round(avg_monthly.get(mid, 50) * rng.uniform(1.0, 2.0), 1)
        tid += 1
        txns.append((tid, mid, HISTORY_START.isoformat(), "OPENING", opening,
                     "OPENING-BAL"))
    # Receipts -> accepted quantity only
    for rid, plid, rec_date, rq, rejected in receipts:
        tid += 1
        txns.append((tid, line_material[plid], rec_date, "RECEIPT",
                     round(rq - rejected, 1), f"RCPT-{rid}"))
    issues = []
    for (mid, d), q in consumption.items():
        tid += 1
        issues.append((tid, mid, d.isoformat(), "ISSUE", -round(q, 1),
                       "PROD-ISSUE"))
    # Occasional adjustments (data-quality realism)
    for _ in range(120):
        mid = rng.choice(materials)[0]
        tid += 1
        issues.append((tid, mid, rdate(HISTORY_START, HISTORY_END).isoformat(),
                       "ADJUSTMENT", round(rng.uniform(-20, 20), 1), "ADJ"))
    txns.extend(issues)
    return txns


# ----------------------------------------------------------------------------
# 3. RISK DATA
# ----------------------------------------------------------------------------
def make_risks(materials, suppliers):
    """A realistic risk register: single-source, disruption, quality, demand."""
    risks, events, actions = [], [], []
    mat_ids = [m[0] for m in materials]
    sup_ids = [s[0] for s in suppliers]
    cats = ["supplier_delivery", "material_availability", "single_source",
            "demand_volatility", "quality", "cost"]
    owners = ["procurement_analyst", "inventory_controller", "production_planner",
              "operations_manager"]
    rid = 0
    # Single-source risks (matches the ~15% single-sourced materials)
    for i in range(4):
        rid += 1
        risks.append((rid, "single_source", rng.choice(mat_ids),
                      rng.choice(sup_ids), rng.randint(2, 4), rng.randint(3, 5),
                      rng.choice(owners), "open"))
    # Disruption-window risks
    for i in range(3):
        rid += 1
        risks.append((rid, "supplier_delivery", rng.choice(mat_ids),
                      f"SUP-{DISRUPTION['supplier_idx']:03d}",
                      rng.randint(3, 5), rng.randint(3, 5),
                      rng.choice(owners), "open"))
    for i in range(5):
        rid += 1
        risks.append((rid, rng.choice(cats), rng.choice(mat_ids),
                      rng.choice(sup_ids), rng.randint(1, 5), rng.randint(1, 5),
                      rng.choice(owners), rng.choice(["open", "mitigating"])))
    # Events: late deliveries cluster in the disruption window
    eid = 0
    for _ in range(20):
        eid += 1
        in_dis = rng.random() < 0.5
        ed = (rdate(DISRUPTION["start"], DISRUPTION["end"]) if in_dis
              else rdate(HISTORY_START, HISTORY_END))
        events.append((eid, rng.randint(1, rid), ed.isoformat(),
                       rng.choice(["late_delivery", "quality_hold",
                                   "demand_spike", "partial_shipment"]),
                       rng.randint(2, 5), round(rng.uniform(5000, 200000), 2),
                       rng.randint(1, 30) if in_dis else rng.randint(0, 5)))
    # Mitigation actions
    aid = 0
    for _ in range(15):
        aid += 1
        actions.append((aid, rng.randint(1, rid),
                        rng.choice(["expedite_po", "alternate_source",
                                    "safety_stock_raise", "reprioritize_orders",
                                    "quality_audit"]),
                        round(rng.uniform(10000, 300000), 2),
                        rdate(HISTORY_START, HISTORY_END).isoformat(),
                        rng.choice(["proposed", "in_progress", "complete"]),
                        rng.choice(["pending", "shortage_avoided",
                                    "partial_relief", "pending"])))
    return risks, events, actions


# ----------------------------------------------------------------------------
# 3b. POLICY / SCENARIO / CALENDAR DATA
# ----------------------------------------------------------------------------
def make_dim_date():
    """Calendar dimension for the full history window."""
    rows, d = [], HISTORY_START
    while d <= HISTORY_END:
        rows.append((d.isoformat(), d.year, d.month, (d.month - 1) // 3 + 1,
                     int(d.strftime("%V")), 1 if d.weekday() >= 5 else 0))
        d += timedelta(days=1)
    return rows


def explode_daily_demand(orders, bom):
    """Gross material requirements per (material, date) — daily grain."""
    bom_by_product = {}
    for _, pid, mid, qpp, scrap, _, _ in bom:
        bom_by_product.setdefault(pid, []).append((mid, qpp, scrap))
    out = {}
    for _, pid, qty, release, req_date, _, status in orders:
        if status == "planned":
            continue
        d = date.fromisoformat(release)
        for mid, qpp, scrap in bom_by_product.get(pid, []):
            key = (mid, d)
            out[key] = out.get(key, 0) + qty * qpp * (1 + scrap)
    return out


def make_policy_params(materials, orders, bom, supplier_material):
    """Baseline inventory-policy parameters per material (seed for optimizer).

    Three baselines, all documented and reproducible:
      weeks_of_cover_4 : SS = 28 days of mean demand (rule of thumb)
      normal_ss_95     : SS = z(0.95) * sigma_d * sqrt(LT + R)  (textbook)
      minmax           : ROP from normal SS; order-up-to = ROP + 30d demand
    The optimizer (Stage 4) appends KDE-based rows with source='optimized'.
    """
    import math
    daily = explode_daily_demand(orders, bom)
    # Per-material daily demand series over history
    n_days = (HISTORY_END - HISTORY_START).days + 1
    stats = {}
    agg = {}
    for (mid, d), q in daily.items():
        agg.setdefault(mid, []).append(q)
    for mid, vals in agg.items():
        mu = sum(vals) / n_days            # mean over ALL history days
        var = sum((v - mu) ** 2 for v in vals) / n_days
        stats[mid] = (mu, math.sqrt(var))
    # Primary-supplier lead time per material
    lt_of = {}
    for sid, mid, lt, price, moq, cap, is_primary in supplier_material:
        if is_primary:
            lt_of[mid] = lt
    z95 = 1.6448536269514722
    rows, pid = [], 0
    for mid, cat, unit, cost, crit, ss in materials:
        mu, sigma = stats.get(mid, (1.0, 1.0))
        lt = lt_of.get(mid, 21)
        R = 7.0  # weekly review
        # B1: weeks of cover
        pid += 1
        ss_woc = mu * 28
        rows.append((pid, mid, "weeks_of_cover_4", 0.95, round(ss_woc, 1),
                     round(mu * (lt + R) + ss_woc, 1),
                     round(mu * (lt + R) + ss_woc + mu * 28, 1), 1.0, "baseline"))
        # B2: normal approximation
        pid += 1
        ss_n = z95 * sigma * math.sqrt(lt + R)
        rop_n = mu * (lt + R) + ss_n
        rows.append((pid, mid, "normal_ss_95", 0.95, round(ss_n, 1),
                     round(rop_n, 1), round(rop_n + mu * 28, 1), 1.0, "baseline"))
        # B3: min-max
        pid += 1
        rows.append((pid, mid, "minmax", 0.95, round(ss_n, 1), round(rop_n, 1),
                     round(rop_n + mu * 30, 1), 1.0, "baseline"))
    return rows


def make_scenario_config():
    """Scenario definitions from config (or defaults)."""
    default = {
        "S0_normal":       (1.0, 1.0, 1.0, "Normal operating conditions"),
        "S1_lt_disrupt":   (1.0, 2.5, 1.0, "Supplier lead-time disruption"),
        "S2_demand_surge":  (1.6, 1.0, 1.0, "Demand surge (+60%)"),
        "S3_quality":       (1.0, 1.0, 4.0, "Quality loss: 4x defect rate"),
        "S4_critical_var":  (1.3, 1.8, 1.0, "High variability, critical items"),
        "S5_combined":      (1.5, 2.2, 2.0, "Combined demand + lead-time shock"),
    }
    cfg_sc = (CFG.get("scenarios") or {})
    rows = []
    for sid, (dm, lm, fm, desc) in default.items():
        if sid in cfg_sc:
            c = cfg_sc[sid]
            dm = float(c.get("demand_mult", dm)); lm = float(c.get("lt_mult", lm))
            fm = float(c.get("defect_mult", fm))
        rows.append((sid, sid.replace("_", " ").title(), dm, lm, fm, desc))
    return rows


# ----------------------------------------------------------------------------
# 4. DATABASE
# ----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE products(product_id TEXT PRIMARY KEY, product_family TEXT,
    unit TEXT, active_flag INTEGER);
CREATE TABLE materials(material_id TEXT PRIMARY KEY, category TEXT, unit TEXT,
    unit_cost REAL, criticality TEXT, safety_stock REAL);
CREATE TABLE bom_lines(bom_line_id INTEGER PRIMARY KEY, product_id TEXT
    REFERENCES products(product_id), material_id TEXT
    REFERENCES materials(material_id), qty_per_product REAL,
    scrap_allowance REAL, effective_from TEXT, effective_to TEXT);
CREATE TABLE suppliers(supplier_id TEXT PRIMARY KEY, region TEXT,
    status TEXT, capacity_band TEXT);
CREATE TABLE supplier_material(supplier_id TEXT REFERENCES suppliers(supplier_id),
    material_id TEXT REFERENCES materials(material_id),
    lead_time_days INTEGER, unit_price REAL, moq REAL, max_capacity REAL,
    is_primary INTEGER, PRIMARY KEY(supplier_id, material_id));
CREATE TABLE production_orders(production_order_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES products(product_id), planned_qty REAL,
    release_date TEXT, required_date TEXT, priority TEXT, status TEXT);
CREATE TABLE purchase_orders(po_id TEXT PRIMARY KEY,
    supplier_id TEXT REFERENCES suppliers(supplier_id), order_date TEXT,
    status TEXT, currency_code TEXT);
CREATE TABLE purchase_order_lines(po_line_id TEXT PRIMARY KEY,
    po_id TEXT REFERENCES purchase_orders(po_id),
    material_id TEXT REFERENCES materials(material_id), order_qty REAL,
    promised_date TEXT, unit_price REAL);
CREATE TABLE goods_receipts(receipt_id INTEGER PRIMARY KEY,
    po_line_id TEXT REFERENCES purchase_order_lines(po_line_id),
    receipt_date TEXT, received_qty REAL, rejected_qty REAL);
CREATE TABLE inventory_transactions(transaction_id INTEGER PRIMARY KEY,
    material_id TEXT REFERENCES materials(material_id), transaction_date TEXT,
    type TEXT, signed_qty REAL, reference_id TEXT);
CREATE TABLE risk_register(risk_id INTEGER PRIMARY KEY, category TEXT,
    material_id TEXT, supplier_id TEXT, likelihood INTEGER, impact INTEGER,
    owner_role TEXT, status TEXT);
CREATE TABLE risk_events(event_id INTEGER PRIMARY KEY,
    risk_id INTEGER REFERENCES risk_register(risk_id), event_date TEXT,
    event_type TEXT, severity INTEGER, estimated_loss REAL, disruption_days INTEGER);
CREATE TABLE mitigation_actions(action_id INTEGER PRIMARY KEY,
    risk_id INTEGER REFERENCES risk_register(risk_id), action_type TEXT,
    estimated_cost REAL, due_date TEXT, status TEXT, outcome TEXT);
CREATE TABLE dim_date(date TEXT PRIMARY KEY, year INTEGER, month INTEGER,
    quarter INTEGER, week INTEGER, is_weekend INTEGER);
CREATE TABLE inventory_policy_params(policy_id INTEGER PRIMARY KEY,
    material_id TEXT REFERENCES materials(material_id), policy_name TEXT,
    service_level REAL, safety_stock REAL, reorder_point REAL,
    order_up_to REAL, review_weeks REAL, source TEXT);
CREATE TABLE scenario_config(scenario_id TEXT PRIMARY KEY, name TEXT,
    demand_mult REAL, lt_mult REAL, defect_mult REAL, description TEXT);
CREATE TABLE simulation_results(result_id INTEGER PRIMARY KEY,
    scenario_id TEXT REFERENCES scenario_config(scenario_id),
    policy_name TEXT, replication INTEGER, fill_rate REAL,
    cycle_service_level REAL, stockout_count INTEGER,
    avg_inventory_value REAL, total_cost REAL);
"""


def build_db(seed: int, out_path: str):
    global rng
    rng = random.Random(seed)
    print(f"[seed={seed}] generating master data...")
    suppliers, tiers = make_suppliers()
    materials = make_materials()
    products = make_products()
    bom = make_bom(products, materials)
    sm = make_supplier_material(materials, suppliers, tiers)

    print("  production orders + requirements...")
    orders = make_production_orders(products, bom)
    req = explode_requirements(orders, bom)
    print("  purchase orders...")
    pos, polines = make_purchase_orders(req, sm, tiers)
    print("  goods receipts...")
    receipts = make_receipts(polines, pos, sm, tiers)
    print("  inventory ledger...")
    line_mat = {plid: mid for plid, _, mid, _, _, _ in polines}
    txns = make_inventory(materials, receipts, orders, bom, line_mat)
    print("  risk data...")
    risks, events, actions = make_risks(materials, suppliers)
    print("  calendar / policy / scenario tables...")
    dimdate = make_dim_date()
    policies = make_policy_params(materials, orders, bom, sm)
    scenarios = make_scenario_config()

    print("  writing database...")
    con = sqlite3.connect(out_path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO products VALUES(?,?,?,?)", products)
    con.executemany("INSERT INTO materials VALUES(?,?,?,?,?,?)", materials)
    con.executemany("INSERT INTO bom_lines VALUES(?,?,?,?,?,?,?)", bom)
    con.executemany("INSERT INTO suppliers VALUES(?,?,?,?)", suppliers)
    con.executemany("INSERT INTO supplier_material VALUES(?,?,?,?,?,?,?)", sm)
    con.executemany("INSERT INTO production_orders VALUES(?,?,?,?,?,?,?)", orders)
    con.executemany("INSERT INTO purchase_orders VALUES(?,?,?,?,?)", pos)
    con.executemany("INSERT INTO purchase_order_lines VALUES(?,?,?,?,?,?)", polines)
    con.executemany("INSERT INTO goods_receipts VALUES(?,?,?,?,?)", receipts)
    con.executemany("INSERT INTO inventory_transactions VALUES(?,?,?,?,?,?)", txns)
    con.executemany("INSERT INTO risk_register VALUES(?,?,?,?,?,?,?,?)", risks)
    con.executemany("INSERT INTO risk_events VALUES(?,?,?,?,?,?,?)", events)
    con.executemany("INSERT INTO mitigation_actions VALUES(?,?,?,?,?,?,?)", actions)
    con.executemany("INSERT INTO dim_date VALUES(?,?,?,?,?,?)", dimdate)
    con.executemany("INSERT INTO inventory_policy_params VALUES(?,?,?,?,?,?,?,?,?)", policies)
    con.executemany("INSERT INTO scenario_config VALUES(?,?,?,?,?,?)", scenarios)
    con.commit()
    return con


def validate(con) -> list:
    """Integrity checks. Returns list of (check, ok, detail)."""
    out = []

    def q(sql, *a):
        return con.execute(sql, a).fetchone()[0]

    # Row counts
    counts = {t: q(f"SELECT COUNT(*) FROM {t}") for t in
              ["products", "materials", "bom_lines", "suppliers",
               "purchase_orders", "purchase_order_lines", "goods_receipts",
               "inventory_transactions", "risk_register", "dim_date",
               "inventory_policy_params", "scenario_config"]}
    out.append(("row_counts", True, counts))

    # FK integrity: every receipt references a real PO line, etc.
    orphans = q("""SELECT COUNT(*) FROM goods_receipts g LEFT JOIN
                  purchase_order_lines p ON g.po_line_id = p.po_line_id
                  WHERE p.po_line_id IS NULL""")
    out.append(("fk_receipts", orphans == 0, f"{orphans} orphans"))
    orphans = q("""SELECT COUNT(*) FROM inventory_transactions t LEFT JOIN
                  materials m ON t.material_id = m.material_id
                  WHERE m.material_id IS NULL""")
    out.append(("fk_inventory", orphans == 0, f"{orphans} orphans"))

    # Business rules
    bad = q("""SELECT COUNT(*) FROM goods_receipts
               WHERE rejected_qty > received_qty OR received_qty < 0""")
    out.append(("receipt_qty_rule", bad == 0, f"{bad} violations"))
    bad = q("""SELECT COUNT(*) FROM purchase_order_lines p JOIN purchase_orders o
               ON p.po_id = o.po_id WHERE p.promised_date < o.order_date""")
    out.append(("date_logic", bad == 0, f"{bad} violations"))
    bad = q("""SELECT COUNT(*) FROM goods_receipts g JOIN purchase_order_lines p
               ON g.po_line_id = p.po_line_id JOIN purchase_orders o
               ON p.po_id = o.po_id WHERE g.receipt_date < o.order_date""")
    out.append(("receipt_after_order", bad == 0, f"{bad} violations"))

    # Inventory reconciliation: no material goes deeply negative vs opening logic
    neg = q("""SELECT COUNT(*) FROM (
                 SELECT material_id, SUM(signed_qty) AS net
                 FROM inventory_transactions GROUP BY material_id
                 HAVING net < -5000)""")
    out.append(("inventory_sanity", neg == 0, f"{neg} materials deeply negative"))

    # BOM coverage: every active product has components
    bare = q("""SELECT COUNT(*) FROM products p LEFT JOIN bom_lines b
                ON p.product_id = b.product_id
                WHERE p.active_flag = 1 AND b.bom_line_id IS NULL""")
    out.append(("bom_coverage", bare == 0, f"{bare} products without BOM"))

    # Policy params: 3 baselines for every material
    npm = q("SELECT COUNT(DISTINCT material_id) FROM inventory_policy_params")
    nm = q("SELECT COUNT(*) FROM materials")
    out.append(("policy_coverage", npm == nm,
                f"{npm}/{nm} materials have baseline policies"))
    # dim_date covers the full history window
    nd = q("SELECT COUNT(*) FROM dim_date")
    expect = (HISTORY_END - HISTORY_START).days + 1
    out.append(("dim_date_coverage", nd == expect, f"{nd}/{expect} days"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/processed/shortage_dss.db")
    ap.add_argument("--config", default="config/project_config.yaml")
    args = ap.parse_args()
    import os
    # Resolve paths relative to the project root (parent of src/)
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    cfg_path = (args.config if os.path.isabs(args.config)
                else os.path.join(root, args.config))
    load_config(cfg_path if os.path.exists(cfg_path) else None)
    out = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if os.path.exists(out):
        os.remove(out)
    con = build_db(args.seed, out)
    print("validation:")
    allok = True
    for name, ok, detail in validate(con):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        allok &= ok
    # apply analytical views so they ship with the database
    _here = os.path.dirname(os.path.abspath(__file__))          # <root>/src/data_generation
    _root = os.path.dirname(os.path.dirname(_here))             # <root>
    views_sql = os.path.join(_root, "database", "views.sql")
    if os.path.exists(views_sql):
        con.executescript(open(views_sql).read())
        nv = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
        print(f"  applied database/views.sql ({nv} views)")
    con.close()
    print(f"wrote {args.out} — {'ALL CHECKS PASSED' if allok else 'FAILURES PRESENT'}")


if __name__ == "__main__":
    main()
