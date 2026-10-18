"""
Monte Carlo (R,S) policy simulator + scenario engine.

Simulates a periodic-review, order-up-to policy under stochastic demand and
stochastic lead times. Two evaluation modes:
  * backtest  : simulate directly on the held-out TEST period (temporal holdout)
  * montecarlo: bootstrap demand from TRAIN + resample empirical lead times,
                R replications -> mean and 95% CI per metric.

Definitions (documented, consistent everywhere):
  * fill_rate (beta)  = units satisfied immediately / total units demanded
  * cycle_service_level (alpha) = fraction of review cycles with zero stockouts
  * stockout_count    = number of days with unfilled demand
  * total_cost        = holding + ordering + stockout penalty (all in INR)

Scenarios scale demand / lead time / defect rate per config/project_config.yaml
and results are written to simulation_results (seeded => reproducible).
"""
import math
import sqlite3
import numpy as np

REVIEW_DAYS = 7
WARMUP_DAYS = 30


def simulate_one(demand, lt_arr, S, unit_cost, h_annual, order_cost,
                 penalty_mult, defect_rate, rng):
    """Single replication of (R,S). Returns metric dict."""
    T = len(demand)
    lt_max = int(np.ceil(lt_arr.max())) + 1
    # Pre-draw lead times for a generous upper bound on orders
    max_orders = T // REVIEW_DAYS + 2
    lt_draws = rng.choice(lt_arr, size=max_orders)
    on_hand = S
    in_transit = []  # (arrival_day, qty)
    satisfied = 0.0
    total_dem = 0.0
    short_units = 0.0
    stockout_days = 0
    n_orders = 0
    inv_sum = 0.0
    inv_days = 0
    cycles = 0
    cycles_clean = 0
    cycle_had_stockout = False
    li = 0
    for t in range(T):
        # arrivals
        still = []
        for arr, q in in_transit:
            if arr <= t:
                on_hand += q * (1 - defect_rate)  # defective units unusable
            else:
                still.append((arr, q))
        in_transit = still
        # demand
        d = demand[t]
        if d > 0:
            total_dem += d
            if on_hand >= d:
                on_hand -= d
                satisfied += d
            else:
                short = d - on_hand
                satisfied += on_hand
                short_units += short
                on_hand = 0.0
                stockout_days += 1
                cycle_had_stockout = True
        # review & order
        if t % REVIEW_DAYS == 0:
            if t >= WARMUP_DAYS:
                cycles += 1
                if not cycle_had_stockout:
                    cycles_clean += 1
                cycle_had_stockout = False
            in_transit_qty = sum(q for _, q in in_transit)
            order_qty = S - (on_hand + in_transit_qty)
            if order_qty > 0:
                lt = max(1, int(round(lt_draws[li]))) if li < len(lt_draws) else 7
                li += 1
                in_transit.append((t + lt, order_qty))
                n_orders += 1
        # holding cost on positive inventory after warmup
        if t >= WARMUP_DAYS and on_hand > 0:
            inv_sum += on_hand
            inv_days += 1
    eff_days = T - WARMUP_DAYS
    fill = satisfied / total_dem if total_dem > 0 else 1.0
    csl = cycles_clean / cycles if cycles > 0 else 1.0
    avg_inv = (inv_sum / inv_days) if inv_days else 0.0
    holding = avg_inv * unit_cost * h_annual / 365 * eff_days
    ordering = n_orders * order_cost
    penalty = short_units * unit_cost * penalty_mult
    return {"fill_rate": fill, "cycle_service_level": csl,
            "stockout_count": stockout_days,
            "avg_inventory_value": avg_inv * unit_cost,
            "total_cost": holding + ordering + penalty,
            "n_orders": n_orders}


def run_montecarlo(demand_train, lt_arr, S, unit_cost, h_annual, order_cost,
                   penalty_mult, defect_rate, T, R, seed,
                   demand_mult=1.0, lt_mult=1.0, defect_mult=1.0):
    """R replications; returns {metric: (mean, lo95, hi95)}."""
    rng = np.random.default_rng(seed)
    n = len(demand_train)
    mets = []
    for r in range(R):
        idx = rng.integers(0, n, size=T)
        dem = demand_train[idx] * demand_mult
        lt_use = lt_arr * lt_mult
        m = simulate_one(dem, lt_use, S, unit_cost, h_annual, order_cost,
                         penalty_mult, defect_rate * defect_mult, rng)
        mets.append(m)
    out = {}
    for k in mets[0]:
        v = np.array([m[k] for m in mets])
        out[k] = (float(v.mean()),
                  float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975)))
    out["n_rep"] = R
    return out


def run_backtest(demand_test, lt_arr, S, unit_cost, h_annual, order_cost,
                 penalty_mult, defect_rate, seed=0):
    """Single run on the held-out test period (no resampling)."""
    rng = np.random.default_rng(seed)
    return simulate_one(np.array(demand_test, dtype=float), lt_arr, S,
                        unit_cost, h_annual, order_cost, penalty_mult,
                        defect_rate, rng)


def run_scenarios(con, estimates, cost, rej, cfg_scenarios, sample_mids,
                  T=180, R=100, seed=99, policies=("weeks_of_cover_4",
                  "normal_ss_95", "kde_ss_95"), h_annual=0.22,
                  order_cost=1500.0, penalty_mult=2.5):
    """Baseline vs proposed under each scenario. Writes simulation_results."""
    rng = np.random.default_rng(seed)
    con.execute("DELETE FROM simulation_results")
    rid = 0
    for scen_id, scen in cfg_scenarios.items():
        dm = scen.get("demand_mult", 1.0); lm = scen.get("lt_mult", 1.0)
        fm = scen.get("defect_mult", 1.0)
        for mid in sample_mids:
            mat = estimates[mid]
            for pname in policies:
                p = mat["policies"][pname]
                mc = run_montecarlo(
                    mat["demand"], mat["lt_samples"], p["S"], cost[mid],
                    h_annual, order_cost, penalty_mult,
                    rej.get(mid, 0.01), T, R, int(rng.integers(1e9)),
                    demand_mult=dm, lt_mult=lm, defect_mult=fm)
                rid += 1
                con.execute(
                    """INSERT INTO simulation_results VALUES(?,?,?,?,?,?,?,?,?)""",
                    (rid, scen_id, pname, R,
                     mc["fill_rate"][0], mc["cycle_service_level"][0],
                     int(mc["stockout_count"][0]),
                     mc["avg_inventory_value"][0], mc["total_cost"][0]))
    con.commit()
    return rid
