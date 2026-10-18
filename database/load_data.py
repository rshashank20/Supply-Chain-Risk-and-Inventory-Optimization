#!/usr/bin/env python3
"""
load_data.py — (re)build the Shortage DSS database from the synthetic generator.

Project : Risk-Aware Safety-Stock Optimization and Material Shortage DSS
DATA    : ALL data is SYNTHETIC, produced by the seeded generator at
          src/data_generation/generate_data.py (default seed 42, business
          assumptions in config/project_config.yaml). Nothing here represents
          a real company, real suppliers, or real transactions.

Usage:
    python3 database/load_data.py            # rebuild with default seed 42
    python3 database/load_data.py --seed 7   # rebuild a reproducible variant

The script runs the generator as a subprocess (so the generator's own
path/CLI handling is honoured), then re-opens the database and prints a
table-count verification summary. Run from anywhere — paths are resolved
relative to the project root (parent of the database/ directory).
"""
import argparse
import os
import sqlite3
import subprocess
import sys

# Project root = parent of the directory containing this script (database/).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATOR = os.path.join(PROJECT_ROOT, "src", "data_generation", "generate_data.py")
DB_PATH = os.path.join(PROJECT_ROOT, "data", "processed", "shortage_dss.db")

TABLES = [
    "products", "materials", "bom_lines", "suppliers", "supplier_material",
    "production_orders", "purchase_orders", "purchase_order_lines",
    "goods_receipts", "inventory_transactions", "risk_register",
    "risk_events", "mitigation_actions", "dim_date",
    "inventory_policy_params", "scenario_config", "simulation_results",
]


def run_generator(seed: int) -> None:
    if not os.path.isfile(GENERATOR):
        sys.exit(f"generator not found: {GENERATOR}")
    print(f"[load_data] running generator (seed={seed})...")
    # Pin PYTHONHASHSEED so str-set iteration order inside the generator
    # (make_bom iterates a set of material ids) is stable across processes;
    # without it, the same seed can still produce different databases.
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    proc = subprocess.run(
        [sys.executable, GENERATOR, "--seed", str(seed)],
        cwd=PROJECT_ROOT,
        capture_output=False,
        env=env,
    )
    if proc.returncode != 0:
        sys.exit(f"[load_data] generator failed with exit code {proc.returncode}")


def verify() -> bool:
    """Print row counts for all 17 tables; return False if anything is off."""
    if not os.path.isfile(DB_PATH):
        print(f"[load_data] FAIL: database not created at {DB_PATH}")
        return False
    con = sqlite3.connect(DB_PATH)
    print("[load_data] verification — row counts:")
    ok = True
    for table in TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        flag = "" if n > 0 or table == "simulation_results" else "  <-- EMPTY (unexpected)"
        if flag:
            ok = False
        print(f"  {table:24s} {n:>8,}{flag}")
    # simulation_results is legitimately empty until experiments run.
    n = con.execute("SELECT COUNT(*) FROM simulation_results").fetchone()[0]
    if n == 0:
        print("  (simulation_results is empty by design — populated by experiments)")
    # FK sanity: receipts must reference real PO lines.
    orphans = con.execute(
        "SELECT COUNT(*) FROM goods_receipts g "
        "LEFT JOIN purchase_order_lines p ON g.po_line_id = p.po_line_id "
        "WHERE p.po_line_id IS NULL"
    ).fetchone()[0]
    print(f"  orphan receipts: {orphans}")
    ok = ok and orphans == 0
    con.close()
    print(f"[load_data] database ready at {DB_PATH} — "
          f"{'VERIFICATION PASSED' if ok else 'VERIFICATION FAILED'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Rebuild the synthetic Shortage DSS database.")
    ap.add_argument("--seed", type=int, default=42,
                    help="RNG seed for the generator (default: 42)")
    args = ap.parse_args()
    run_generator(args.seed)
    if not verify():
        sys.exit(1)


if __name__ == "__main__":
    main()
