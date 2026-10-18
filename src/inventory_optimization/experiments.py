"""
Experiments E1–E5: the empirical core of the project.

  E1  KDE vs normal at 95% SL (core RQ): paired comparison of fill rate,
      cycle service level and total cost across sample materials, with 95% CIs.
      Plus a direct backtest on the temporal holdout.
  E2  Cost-vs-service Pareto: KDE at 90/95/98 vs all baselines.
  E3  Lead-time ablation: stochastic LT vs deterministic LT=mean — how much
      safety stock is driven by lead-time variability?
  E4  Sensitivity: holding-cost rate x penalty multiplier grid; ranking stability.
  E5  Disruption: baseline vs KDE under scenario S1 (lead-time disruption).

All inputs estimated on TRAIN (first 18 months). Monte Carlo bootstraps train
demand; the backtest uses the held-out TEST months untouched by estimation.
Negative results are reported honestly (see docs/experiment_results.md).
"""
import json
import os
import sqlite3
import sys
import numpy as np
from scipy import stats as sstats

# Make <root>/src importable regardless of working directory.
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from inventory_optimization.safety_stock import (
    load_series, build_arrays, estimate_policies)
from simulation.simulator import run_montecarlo, run_backtest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVICE_LEVELS = [0.90, 0.95, 0.98]
BASELINE_95 = ["weeks_of_cover_4", "normal_ss_95", "minmax"]
PROPOSED_95 = ["empirical_q_95", "kde_ss_95"]


def stratified_sample(con, estimates, n_per_class=10, seed=11):
    rng = np.random.default_rng(seed)
    crit = {r[0]: r[1] for r in con.execute(
        "SELECT material_id, criticality FROM materials").fetchall()}
    by_c = {}
    for mid in estimates:
        by_c.setdefault(crit.get(mid, "C"), []).append(mid)
    sample = []
    for c in ["A", "B", "C"]:
        pool = sorted(by_c.get(c, []))
        sample += list(rng.choice(pool, size=min(n_per_class, len(pool)),
                                  replace=False))
    return sorted(sample)


def mc_for(con, estimates, cost, rej, mids, policies, R, seed, T=180,
           h_annual=0.22, order_cost=1500.0, penalty_mult=2.5,
           demand_mult=1.0, lt_mult=1.0, defect_mult=1.0):
    """Run MC for (materials x policies). Returns {(mid,pname): metrics}."""
    rng = np.random.default_rng(seed)
    out = {}
    for mid in mids:
        mat = estimates[mid]
        for pname in policies:
            p = mat["policies"][pname]
            out[(mid, pname)] = run_montecarlo(
                mat["demand"], mat["lt_samples"], p["S"], cost[mid],
                h_annual, order_cost, penalty_mult, rej.get(mid, 0.01),
                T, R, int(rng.integers(1e9)),
                demand_mult=demand_mult, lt_mult=lt_mult, defect_mult=defect_mult)
    return out


def paired_report(res_a, res_b, mids, metric):
    """Paired comparison of policy A vs B across materials (mean of MC means)."""
    a = np.array([res_a[(m,)][metric][0] for m in mids])
    b = np.array([res_b[(m,)][metric][0] for m in mids])
    d = b - a
    n = len(d)
    se = d.std(ddof=1) / np.sqrt(n) if n > 1 else 0.0
    tcrit = sstats.t.ppf(0.975, n - 1) if n > 1 else 0.0
    w = sstats.wilcoxon(d, alternative="two-sided") if n > 7 else None
    return {"mean_a": float(a.mean()), "mean_b": float(b.mean()),
            "mean_diff_b_minus_a": float(d.mean()),
            "ci95": [float(d.mean() - tcrit * se), float(d.mean() + tcrit * se)],
            "wilcoxon_p": float(w.pvalue) if w else None, "n": n}


def backtest_all(con, estimates, cost, rej, mids, policies, test_dem,
                 h_annual=0.22, order_cost=1500.0, penalty_mult=2.5):
    out = {}
    for mid in mids:
        mat = estimates[mid]
        for pname in policies:
            p = mat["policies"][pname]
            out[(mid, pname)] = run_backtest(
                test_dem[mid], mat["lt_samples"], p["S"], cost[mid],
                h_annual, order_cost, penalty_mult, rej.get(mid, 0.01))
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/processed/shortage_dss.db")
    ap.add_argument("--out", default="data/processed/experiment_results.json")
    ap.add_argument("--reps", type=int, default=100)
    args = ap.parse_args()
    db = args.db if os.path.isabs(args.db) else os.path.join(ROOT, args.db)
    outp = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    con = sqlite3.connect(db)

    print("loading series + estimating policies (train only)...")
    dem_rows, lt_rows, rej, cost = load_series(con)
    dem, lts, _ = build_arrays(dem_rows, lt_rows)
    estimates = estimate_policies(dem, lts, SERVICE_LEVELS)
    mids = stratified_sample(con, estimates)
    print(f"sample: {len(mids)} materials")

    # test-period demand for backtest
    dem_test_rows, _, _, _ = load_series(con, train_end="2026-09-30")
    dem_test, _, _ = build_arrays(dem_test_rows, lt_rows,
                                  train_start="2026-04-01", train_end="2026-09-30")
    results = {"sample_materials": mids, "n_rep": args.reps}

    # ---- E1: KDE vs normal @95 ----
    print("E1: kde vs normal @95 ...")
    r1 = mc_for(con, estimates, cost, rej, mids,
                ["normal_ss_95", "kde_ss_95"], args.reps, seed=101)
    e1 = {}
    for metric in ["fill_rate", "cycle_service_level", "total_cost",
                   "avg_inventory_value", "stockout_count"]:
        ra = {(m,): r1[(m, "normal_ss_95")] for m in mids}
        rb = {(m,): r1[(m, "kde_ss_95")] for m in mids}
        e1[metric] = paired_report(ra, rb, mids, metric)
    # backtest on holdout
    bt = backtest_all(con, estimates, cost, rej, mids,
                      ["normal_ss_95", "kde_ss_95"], dem_test)
    e1["backtest"] = {
        m: {"normal": {k: bt[(m, "normal_ss_95")][k] for k in
                       ["fill_rate", "cycle_service_level", "total_cost"]},
            "kde": {k: bt[(m, "kde_ss_95")][k] for k in
                    ["fill_rate", "cycle_service_level", "total_cost"]}}
        for m in mids}
    results["E1_kde_vs_normal_95"] = e1

    # ---- E2: Pareto 90/95/98 ----
    print("E2: pareto ...")
    pareto_pols = (["weeks_of_cover_4", "normal_ss_95", "minmax",
                    "empirical_q_95", "kde_ss_95"]
                   + [f"kde_ss_{int(s*100)}" for s in (0.90, 0.98)]
                   + [f"normal_ss_{int(s*100)}" for s in (0.90, 0.98)])
    r2 = mc_for(con, estimates, cost, rej, mids, pareto_pols,
                max(50, args.reps // 2), seed=202)
    e2 = {}
    for pname in pareto_pols:
        fr = np.mean([r2[(m, pname)]["fill_rate"][0] for m in mids])
        tc = np.mean([r2[(m, pname)]["total_cost"][0] for m in mids])
        e2[pname] = {"mean_fill_rate": float(fr), "mean_total_cost": float(tc)}
    results["E2_pareto"] = e2

    # ---- E3: lead-time ablation ----
    print("E3: LT ablation ...")
    r3a = mc_for(con, estimates, cost, rej, mids, ["kde_ss_95"],
                 max(50, args.reps // 2), seed=303)
    # deterministic LT: collapse lt samples to their mean
    est_det = {m: dict(v) for m, v in estimates.items()}
    for m in mids:
        est_det[m] = dict(estimates[m])
        est_det[m]["lt_samples"] = np.full_like(estimates[m]["lt_samples"],
                                                estimates[m]["lt_samples"].mean())
    r3b = mc_for(con, est_det, cost, rej, mids, ["kde_ss_95"],
                 max(50, args.reps // 2), seed=303)
    e3 = {}
    for metric in ["total_cost", "fill_rate"]:
        a = np.array([r3a[(m, "kde_ss_95")][metric][0] for m in mids])
        b = np.array([r3b[(m, "kde_ss_95")][metric][0] for m in mids])
        e3[metric] = {"stochastic_LT": float(a.mean()),
                      "deterministic_LT": float(b.mean())}
    # SS attributable to LT variability: compare SS levels is in policies already;
    # report cost deltas here
    results["E3_lt_ablation"] = e3

    # ---- E4: sensitivity ----
    print("E4: sensitivity ...")
    e4 = {}
    for h in [0.15, 0.22, 0.30]:
        for pen in [1.5, 2.5, 4.0]:
            r4 = mc_for(con, estimates, cost, rej, mids,
                        ["normal_ss_95", "kde_ss_95"], 50, seed=404,
                        h_annual=h, penalty_mult=pen)
            tc_n = np.mean([r4[(m, "normal_ss_95")]["total_cost"][0] for m in mids])
            tc_k = np.mean([r4[(m, "kde_ss_95")]["total_cost"][0] for m in mids])
            e4[f"h{h}_pen{pen}"] = {"normal_cost": float(tc_n),
                                    "kde_cost": float(tc_k),
                                    "kde_wins": bool(tc_k < tc_n)}
    results["E4_sensitivity"] = e4

    # ---- E5: disruption scenario ----
    print("E5: disruption ...")
    r5 = mc_for(con, estimates, cost, rej, mids,
                ["weeks_of_cover_4", "normal_ss_95", "kde_ss_95"], 100,
                seed=505, lt_mult=2.5, demand_mult=1.2)
    e5 = {}
    for pname in ["weeks_of_cover_4", "normal_ss_95", "kde_ss_95"]:
        e5[pname] = {
            "mean_fill_rate": float(np.mean([r5[(m, pname)]["fill_rate"][0] for m in mids])),
            "mean_total_cost": float(np.mean([r5[(m, pname)]["total_cost"][0] for m in mids])),
            "mean_stockouts": float(np.mean([r5[(m, pname)]["stockout_count"][0] for m in mids]))}
    results["E5_disruption"] = e5

    with open(outp, "w") as f:
        json.dump(results, f, indent=1)
    print(f"wrote {outp}")

    # console headline
    d = e1["total_cost"]
    print(f"E1 total_cost: normal={d['mean_a']:,.0f} kde={d['mean_b']:,.0f} "
          f"diff={d['mean_diff_b_minus_a']:,.0f} 95%CI=[{d['ci95'][0]:,.0f},{d['ci95'][1]:,.0f}] "
          f"p={d['wilcoxon_p']}")
    con.close()


if __name__ == "__main__":
    main()
