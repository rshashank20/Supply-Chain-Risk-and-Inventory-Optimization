"""Simulator invariant tests: the (R,S) Monte Carlo engine must obey
physical and statistical laws regardless of parameters.

Run: python3 tests/test_simulator.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
import numpy as np
from simulation.simulator import simulate_one, run_montecarlo


def _ctx(seed=0):
    rng = np.random.default_rng(seed)
    demand = rng.poisson(50, 180).astype(float)
    lt = np.array([10, 12, 14, 11, 13] * 20, dtype=float)
    return rng, demand, lt


def test_huge_S_no_stockouts():
    rng, demand, lt = _ctx()
    m = simulate_one(demand, lt, S=1e9, unit_cost=100, h_annual=0.22,
                     order_cost=1500, penalty_mult=2.5, defect_rate=0.0, rng=rng)
    assert m["fill_rate"] == 1.0, m
    assert m["stockout_count"] == 0, m


def test_zero_S_no_fill():
    rng, demand, lt = _ctx()
    m = simulate_one(demand, lt, S=0.0, unit_cost=100, h_annual=0.22,
                     order_cost=1500, penalty_mult=2.5, defect_rate=0.0, rng=rng)
    assert m["fill_rate"] < 0.05, m  # only warmup/first-day effects


def test_monotonic_fill_in_S():
    rng, demand, lt = _ctx()
    fills = []
    for S in (500, 2000, 8000):
        r = np.random.default_rng(0)
        m = simulate_one(demand.copy(), lt, S=S, unit_cost=100, h_annual=0.22,
                         order_cost=1500, penalty_mult=2.5, defect_rate=0.0, rng=r)
        fills.append(m["fill_rate"])
    assert fills[0] <= fills[1] <= fills[2], fills


def test_reproducible_seed():
    _, demand, lt = _ctx()
    kw = dict(unit_cost=100, h_annual=0.22, order_cost=1500, penalty_mult=2.5,
              defect_rate=0.01)
    m1 = simulate_one(demand.copy(), lt, S=3000, rng=np.random.default_rng(42), **kw)
    m2 = simulate_one(demand.copy(), lt, S=3000, rng=np.random.default_rng(42), **kw)
    assert m1 == m2


def test_cost_accounting_sane():
    """total_cost = holding + ordering + penalty; all components >= 0."""
    rng, demand, lt = _ctx()
    m = simulate_one(demand, lt, S=3000, unit_cost=100, h_annual=0.22,
                     order_cost=1500, penalty_mult=2.5, defect_rate=0.02, rng=rng)
    assert m["total_cost"] > 0
    assert m["avg_inventory_value"] >= 0
    assert 0.0 <= m["fill_rate"] <= 1.0
    assert 0.0 <= m["cycle_service_level"] <= 1.0
    assert m["n_orders"] > 0


def test_montecarlo_ci_ordering():
    rng_demand = np.random.default_rng(0)
    demand = rng_demand.poisson(50, 730).astype(float)
    lt = np.array([10, 12, 14, 11, 13] * 20, dtype=float)
    out = run_montecarlo(demand, lt, S=3000, unit_cost=100, h_annual=0.22,
                         order_cost=1500, penalty_mult=2.5, defect_rate=0.0,
                         T=180, R=50, seed=5)
    for k, v in out.items():
        if k == "n_rep":
            continue
        mean, lo, hi = v
        assert lo <= mean <= hi, (k, mean, lo, hi)
    assert out["n_rep"] == 50


def test_defects_reduce_effective_supply():
    _, demand, lt = _ctx()
    kw = dict(unit_cost=100, h_annual=0.22, order_cost=1500, penalty_mult=2.5)
    m0 = simulate_one(demand.copy(), lt, S=3000, defect_rate=0.0,
                      rng=np.random.default_rng(9), **kw)
    m1 = simulate_one(demand.copy(), lt, S=3000, defect_rate=0.30,
                      rng=np.random.default_rng(9), **kw)
    assert m1["fill_rate"] <= m0["fill_rate"] + 1e-9, (m0, m1)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all simulator invariant tests passed")
