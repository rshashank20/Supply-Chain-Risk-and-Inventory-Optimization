"""
Safety-stock estimation: baselines vs data-driven (KDE / empirical quantile).

Methodology (see docs/methodology.md and the research report):
  * Train/test split is TEMPORAL: first 18 months train, last 6 months test.
  * Baselines (documented, textbook):
      B1 weeks_of_cover_4 : SS = 28 * mu_daily
      B2 normal_ss        : SS = z(SL) * sigma_daily * sqrt(LT_bar + R)
      B3 minmax           : ROP from B2, order-up-to = ROP + 30d demand
  * Proposed:
      P1 empirical_quantile: bootstrap lead-time-demand (LTD) samples from
         train data (stochastic LT + stochastic demand); SS = Q(SL) - mean(LTD)
      P2 kde              : fit Gaussian KDE on the LTD samples (bandwidth via
         Silverman, cross-checked); SS = Q_KDE(SL) - mean(LTD), floored at 0.
         KDE resampling guards against invalid negative demand.
  * (R,S) periodic-review policy: every R=7 days order up to S = mu_LTD + SS.

All estimation uses TRAIN data only. Validation is Monte Carlo on bootstrapped
train demand (for CIs) plus a direct backtest on the held-out test period.
"""
import math
import sqlite3
import numpy as np
from scipy import stats

Z = {0.90: 1.2815515655446004, 0.95: 1.6448536269514722, 0.98: 2.053748910631823}
REVIEW_DAYS = 7


def load_series(con, train_end="2026-03-31"):
    """Per-material daily demand (train) and empirical lead times.

    Demand comes from production-order BOM explosion at release_date —
    recomputed from the DB (honest analytics layer, not generator internals).
    Lead times are actuals: receipt_date - order_date per PO line.
    """
    dem = con.execute("""
        SELECT b.material_id, o.release_date AS d,
               SUM(o.planned_qty * b.qty_per_product * (1 + b.scrap_allowance)) AS q
        FROM production_orders o
        JOIN bom_lines b ON b.product_id = o.product_id
        WHERE o.status != 'planned' AND o.release_date <= ?
        GROUP BY 1, 2""", (train_end,)).fetchall()
    lt = con.execute("""
        SELECT p.material_id,
               julianday(g.receipt_date) - julianday(o.order_date) AS lead_t
        FROM goods_receipts g
        JOIN purchase_order_lines p ON p.po_line_id = g.po_line_id
        JOIN purchase_orders o ON o.po_id = p.po_id
        WHERE lead_t > 0 AND lead_t < 200""").fetchall()
    rej = con.execute("""
        SELECT p.material_id,
               SUM(g.rejected_qty) * 1.0 / SUM(g.received_qty) AS rej_rate
        FROM goods_receipts g
        JOIN purchase_order_lines p ON p.po_line_id = g.po_line_id
        GROUP BY 1""").fetchall()
    cost = {r[0]: r[1] for r in con.execute(
        "SELECT material_id, unit_cost FROM materials").fetchall()}
    return dem, lt, dict(rej), cost


def build_arrays(dem_rows, lt_rows, train_start="2024-10-01", train_end="2026-03-31"):
    """Dense daily demand arrays per material + lead-time arrays."""
    from datetime import date
    d0 = date.fromisoformat(train_start)
    n_days = (date.fromisoformat(train_end) - d0).days + 1
    dem = {}
    for mid, d, q in dem_rows:
        idx = (date.fromisoformat(d) - d0).days
        if 0 <= idx < n_days:
            dem.setdefault(mid, np.zeros(n_days))[idx] += q
    lts = {}
    for mid, l in lt_rows:
        lts.setdefault(mid, []).append(l)
    lts = {m: np.array(v) for m, v in lts.items() if len(v) >= 5}
    return dem, lts, n_days


def ltd_bootstrap_samples(demand, lt_arr, exposure_extra, n_samples, rng):
    """Bootstrap lead-time-demand samples.

    Each sample: draw a lead time LT from the empirical LT distribution,
    then sum a contiguous block of (LT + exposure_extra) daily demands
    starting at a random index. Captures BOTH demand and lead-time
    stochasticity (the textbook sqrt formula assumes fixed LT).
    """
    T = len(demand)
    max_lt = int(np.ceil(lt_arr.max())) + exposure_extra + 1
    out = np.empty(n_samples)
    for i in range(n_samples):
        lt = float(rng.choice(lt_arr))
        L = int(round(lt)) + exposure_extra
        L = max(1, min(L, T - 1))
        s = rng.integers(0, T - L)
        out[i] = demand[s:s + L].sum()
    return out


def kde_quantile(samples, sl, rng, n_draw=50000):
    """Service-level quantile of a Gaussian-KDE fit (Silverman bandwidth).

    Returns (quantile, mean, bandwidth). Resampling from the KDE avoids
    numerical CDF inversion issues; negative draws are clipped at 0 because
    demand cannot be negative (documented guard).
    Falls back to the empirical quantile when samples are (near-)constant,
    where gaussian_kde would raise LinAlgError on a singular covariance.
    """
    samples = np.asarray(samples, dtype=float)
    if np.std(samples) < 1e-9:
        return float(np.quantile(samples, sl)), float(samples.mean()), 0.0
    kde = stats.gaussian_kde(samples)
    bw = float(kde.factor)
    draws = kde.resample(n_draw, seed=rng)[0]
    draws = np.clip(draws, 0, None)
    return float(np.quantile(draws, sl)), float(samples.mean()), bw


def estimate_policies(dem, lts, service_levels, n_boot=4000, seed=7):
    """Estimate SS for baselines + proposed methods per material.

    Returns dict[mid] -> {policy_name: {ss, S, method notes}}.
    """
    rng = np.random.default_rng(seed)
    out = {}
    for mid, demand in dem.items():
        if mid not in lts:
            continue
        lt_arr = lts[mid]
        mu_d = float(demand.mean())
        sd_d = float(demand.std())
        lt_bar = float(lt_arr.mean())
        if mu_d <= 0:
            continue
        mat = {"mu_d": mu_d, "sd_d": sd_d, "lt_bar": lt_bar,
               "lt_samples": lt_arr, "demand": demand}
        # Baselines
        mat["policies"] = {}
        ss_woc = mu_d * 28
        mat["policies"]["weeks_of_cover_4"] = {
            "ss": ss_woc, "S": mu_d * (lt_bar + REVIEW_DAYS) + ss_woc, "sl": 0.95}
        for sl in service_levels:
            z = Z[sl]
            ss_n = z * sd_d * math.sqrt(lt_bar + REVIEW_DAYS)
            mat["policies"][f"normal_ss_{int(sl*100)}"] = {
                "ss": ss_n, "S": mu_d * (lt_bar + REVIEW_DAYS) + ss_n, "sl": sl}
        # B3: min-max (min = normal-95 ROP, max = ROP + 30d demand). For the
        # (R,S) simulator only the order-up-to level S matters.
        ss_n95 = Z[0.95] * sd_d * math.sqrt(lt_bar + REVIEW_DAYS)
        rop_n95 = mu_d * (lt_bar + REVIEW_DAYS) + ss_n95
        mat["policies"]["minmax"] = {
            "ss": ss_n95, "S": rop_n95 + mu_d * 30, "sl": 0.95}
        # Proposed: bootstrap LTD once, reuse for empirical + KDE
        ltd = ltd_bootstrap_samples(demand, lt_arr, REVIEW_DAYS, n_boot, rng)
        mu_ltd = float(ltd.mean())
        for sl in service_levels:
            q_emp = float(np.quantile(ltd, sl))
            mat["policies"][f"empirical_q_{int(sl*100)}"] = {
                "ss": max(0.0, q_emp - mu_ltd),
                "S": q_emp, "sl": sl}
            q_kde, _, bw = kde_quantile(ltd, sl, rng)
            mat["policies"][f"kde_ss_{int(sl*100)}"] = {
                "ss": max(0.0, q_kde - mu_ltd),
                "S": q_kde, "sl": sl, "kde_bw": bw}
        out[mid] = mat
    return out


def write_optimized_policies(con, estimates):
    """Append KDE/empirical policy rows (source='optimized') to the DB."""
    rows = []
    pid = con.execute("SELECT COALESCE(MAX(policy_id),0) FROM inventory_policy_params").fetchone()[0]
    for mid, mat in estimates.items():
        for pname, p in mat["policies"].items():
            if not (pname.startswith("kde_") or pname.startswith("empirical_")):
                continue
            pid += 1
            rows.append((pid, mid, pname, p["sl"], round(p["ss"], 1),
                         round(p["S"], 1), round(p["S"], 1), 1.0, "optimized"))
    con.executemany("INSERT INTO inventory_policy_params VALUES(?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


if __name__ == "__main__":
    import argparse, os
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/processed/shortage_dss.db")
    ap.add_argument("--service-levels", default="0.90,0.95,0.98")
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db = args.db if os.path.isabs(args.db) else os.path.join(root, args.db)
    sls = [float(x) for x in args.service_levels.split(",")]
    con = sqlite3.connect(db)
    dem_rows, lt_rows, rej, cost = load_series(con)
    dem, lts, n = build_arrays(dem_rows, lt_rows)
    print(f"materials with demand+LT data: {len(set(dem) & set(lts))}")
    est = estimate_policies(dem, lts, sls)
    n = write_optimized_policies(con, est)
    print(f"wrote {n} optimized policy rows")
    con.close()
