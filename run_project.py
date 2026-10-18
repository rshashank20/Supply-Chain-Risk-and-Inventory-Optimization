"""
End-to-end project runner.

    python3 run_project.py                # full pipeline (default seed 42)
    python3 run_project.py --seed 7       # reproducibility variant
    python3 run_project.py --skip-experiments   # faster: DB + SQL only

Stages:
  1. Generate synthetic data  -> data/processed/shortage_dss.db
  2. Estimate safety-stock policies (train) -> inventory_policy_params
  3. Run experiments E1-E5     -> data/processed/experiment_results.json
  4. Run test suite           -> console report
Every stage is seeded; the same seed reproduces the same outputs.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")


def sh(cmd, desc):
    print(f"\n=== {desc} ===")
    print("$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print(f"FAILED: {desc}")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--skip-experiments", action="store_true")
    ap.add_argument("--reps", type=int, default=100,
                    help="Monte Carlo replications per experiment cell")
    args = ap.parse_args()
    py = sys.executable

    sh([py, "src/data_generation/generate_data.py", "--seed", str(args.seed)],
       "Stage 1: synthetic data generation + validation")
    sh([py, "src/inventory_optimization/safety_stock.py"],
       "Stage 2: safety-stock estimation (train) -> inventory_policy_params")

    if not args.skip_experiments:
        sh([py, "src/inventory_optimization/experiments.py",
            "--reps", str(args.reps)],
           "Stage 3: experiments E1-E5 (Monte Carlo + backtest)")
        sh([py, "src/simulation/run_scenarios.py"],
           "Stage 3b: 6-scenario baseline-vs-proposed comparison")

    sh([py, "tests/test_safety_stock.py"], "Stage 4a: safety-stock math tests")
    sh([py, "tests/test_simulator.py"], "Stage 4b: simulator invariant tests")

    # risk engine (optional: runs if present and DB supports it)
    risk_py = os.path.join(SRC, "risk_management", "risk_engine.py")
    if os.path.exists(risk_py):
        sh([py, risk_py], "Stage 5: risk engine -> data/processed/risk_scores.csv")

    print("\nALL STAGES COMPLETE.")
    print("Outputs:")
    print("  data/processed/shortage_dss.db")
    print("  data/processed/experiment_results.json")
    print("  data/processed/risk_scores.csv (if risk engine ran)")


if __name__ == "__main__":
    main()
