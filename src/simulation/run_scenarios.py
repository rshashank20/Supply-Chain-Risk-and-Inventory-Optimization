"""
Populate simulation_results: 6 scenarios x 3 policies on a stratified sample.

Reads scenarios from config/project_config.yaml, policies from the trained
inventory_optimization estimates, writes to simulation_results in the DB.
Seeded -> reproducible.
"""
import os
import sqlite3
import sys
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np
from inventory_optimization.safety_stock import load_series, build_arrays, estimate_policies
from simulation.simulator import run_scenarios

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    db = os.path.join(ROOT, "data", "processed", "shortage_dss.db")
    con = sqlite3.connect(db)
    dem_rows, lt_rows, rej, cost = load_series(con)
    dem, lts, _ = build_arrays(dem_rows, lt_rows)
    estimates = estimate_policies(dem, lts, [0.95])

    rng = np.random.default_rng(11)
    crit = {r[0]: r[1] for r in
            con.execute("SELECT material_id, criticality FROM materials").fetchall()}
    by_c = {}
    for mid in estimates:
        by_c.setdefault(crit.get(mid, "C"), []).append(mid)
    sample = sorted(sum(
        [list(rng.choice(sorted(by_c[c]), size=min(8, len(by_c[c])), replace=False))
         for c in "ABC"], []))
    print(f"sample: {len(sample)} materials")

    cfg = yaml.safe_load(open(os.path.join(ROOT, "config", "project_config.yaml")))
    n = run_scenarios(con, estimates, cost, rej, cfg["scenarios"],
                      sample, T=180, R=60, seed=99)
    print(f"wrote {n} rows to simulation_results")
    con.close()


if __name__ == "__main__":
    main()
