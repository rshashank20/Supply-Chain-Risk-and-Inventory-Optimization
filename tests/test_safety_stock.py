"""Mathematical correctness tests for safety-stock estimation.

Run: python3 -m pytest tests/test_safety_stock.py -v
(or: python3 tests/test_safety_stock.py)
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
import numpy as np
from inventory_optimization.safety_stock import (
    Z, kde_quantile, ltd_bootstrap_samples, estimate_policies)


def test_normal_quantiles():
    """Z-values match textbook standard-normal quantiles."""
    assert abs(Z[0.95] - 1.6448536269514722) < 1e-9
    assert abs(Z[0.90] - 1.2815515655446004) < 1e-9
    assert abs(Z[0.98] - 2.053748910631823) < 1e-9


def test_kde_recovers_normal_quantile():
    """On truly normal data, KDE quantile ≈ theoretical normal quantile."""
    rng = np.random.default_rng(0)
    samples = rng.normal(1000, 100, size=20000)
    q_kde, mean, bw = kde_quantile(samples, 0.95, rng, n_draw=20000)
    q_true = 1000 + 1.6448536269514722 * 100
    assert abs(q_kde - q_true) / q_true < 0.05, f"{q_kde} vs {q_true}"
    assert bw > 0


def test_ss_nonnegative_on_spiky_demand():
    """Safety stock is never negative, even for intermittent demand."""
    rng = np.random.default_rng(1)
    # intermittent: many zeros, occasional spikes
    demand = np.where(rng.random(730) < 0.2, rng.integers(50, 500, 730), 0).astype(float)
    lt = np.array([10, 12, 14, 11, 13] * 20, dtype=float)
    ltd = ltd_bootstrap_samples(demand, lt, 7, 2000, rng)
    for sl in (0.90, 0.95, 0.98):
        q, mu, _ = kde_quantile(ltd, sl, rng)
        assert max(0.0, q - mu) >= 0


def test_constant_demand_zero_ss():
    """Constant demand + fixed LT => ~zero safety stock needed."""
    rng = np.random.default_rng(2)
    demand = np.full(730, 100.0)
    lt = np.full(100, 14.0)
    ltd = ltd_bootstrap_samples(demand, lt, 7, 2000, rng)
    q, mu, _ = kde_quantile(ltd, 0.95, rng)
    # LTD is nearly constant (14+7)*100 = 2100; SS should be tiny
    assert abs((q - mu)) < 2100 * 0.05, f"SS={q-mu} on constant demand"


def test_estimate_policies_structure():
    """estimate_policies returns expected policy keys with sane values."""
    rng = np.random.default_rng(3)
    demand = np.abs(rng.normal(50, 15, 730))
    lt = np.abs(rng.normal(20, 5, 100)) + 1
    est = estimate_policies({"MAT-TEST": demand}, {"MAT-TEST": lt}, [0.95])
    pols = est["MAT-TEST"]["policies"]
    assert "weeks_of_cover_4" in pols and "normal_ss_95" in pols
    assert "kde_ss_95" in pols and "empirical_q_95" in pols and "minmax" in pols
    for p in pols.values():
        assert p["ss"] >= 0 and p["S"] > 0
    # higher service level => higher SS for KDE
    est2 = estimate_policies({"MAT-TEST": demand}, {"MAT-TEST": lt}, [0.90, 0.98])
    assert (est2["MAT-TEST"]["policies"]["kde_ss_98"]["ss"] >=
            est2["MAT-TEST"]["policies"]["kde_ss_90"]["ss"])


def test_ltd_bootstrap_shape():
    """Bootstrap respects stochastic LT: higher LT variance => wider LTD."""
    rng = np.random.default_rng(4)
    demand = np.full(730, 100.0)
    lt_fixed = np.full(100, 14.0)
    lt_var = np.array([5, 10, 14, 20, 30] * 20, dtype=float)
    s_fixed = ltd_bootstrap_samples(demand, lt_fixed, 7, 3000, rng)
    s_var = ltd_bootstrap_samples(demand, lt_var, 7, 3000, rng)
    assert s_var.std() > s_fixed.std(), "LT variability must widen LTD"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all safety-stock math tests passed")
