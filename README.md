# Risk-Aware Safety-Stock Optimization and Material Shortage Decision Support System

**For engineered-component manufacturing (synthetic case study: Spectrum Tool Engineers Pvt. Ltd.)**

> **Data honesty:** every record in this project is synthetically generated
> (seeded Python generator). Nothing here represents real company transactions,
> performance, or savings. All "results" are computed from the synthetic data
> and the documented models — see `docs/limitations.md`.

## What this does

A manufacturing planner faces two linked questions: **how much inventory to hold**
(safety-stock optimization) and **which orders are at risk right now**
(shortage detection + risk prioritization). This project builds one integrated
system that answers both:

1. **Safety-stock core (research contribution):** estimates demand and lead-time
   distributions from history; compares textbook normal-approximation safety
   stock vs data-driven KDE/empirical-quantile methods; validates with Monte
   Carlo simulation on a temporal holdout, with 95% confidence intervals.
2. **Shortage engine (operational application):** recursive BOM explosion in SQL,
   time-phased projected inventory, shortage flags, supplier risk scorecards.
3. **Risk module:** ISO-31000-style risk register with probability-impact scoring
   (ordinal — never treated as calibrated probabilities) and mitigation actions.
4. **Scenario engine:** 6 disruption scenarios; baseline vs proposed policies
   compared under identical simulated conditions.

## Key computed results (from `data/processed/experiment_results.json`)

- **E1 (core RQ):** KDE safety stock vs normal approximation at 95% service —
  mean total-cost difference **−₹116k per material** (KDE cheaper),
  Wilcoxon p=0.036, 95% CI [−₹296k, +₹64k]; backtest on held-out 6 months
  agrees (KDE fill rate 0.951 vs 0.943). Effect is real but heterogeneous —
  reported honestly with wide CIs.
- **E4 (sensitivity):** KDE beats normal on total cost in **9/9**
  holding-cost × penalty combinations.
- **E5 (disruption):** under a 2.5× lead-time shock, all policies degrade
  (fill 0.63–0.75) — no safety-stock policy fully insures a disruption;
  KDE still leads (0.754 vs 0.719).
- Caveat: with the modeled 2.5× stockout penalty, very-high-service policies
  (e.g., min-max) minimize modeled cost — the absolute ranking is
  penalty-parameter-driven (see `docs/experiment_results.md`).

## Reproduce

```bash
pip install -r requirements.txt
python3 run_project.py            # full pipeline (~5 min)
python3 run_project.py --seed 7   # independent reproducibility check
```

Outputs: `data/processed/shortage_dss.db` (17 tables),
`data/processed/experiment_results.json`, `data/processed/risk_scores.csv`.

## Structure

| Path | Contents |
|---|---|
| `config/` | All business assumptions (`project_config.yaml`) — seed, tiers, scenarios |
| `src/data_generation/` | Seeded synthetic-data generator (17 tables) + validation suite |
| `src/inventory_optimization/` | Safety-stock estimation (baselines, KDE) + experiments E1–E5 |
| `src/simulation/` | (R,S) Monte Carlo simulator + scenario engine |
| `src/risk_management/` | Risk scoring engine → `risk_scores.csv` |
| `database/` | `schema.sql`, `load_data.py`, `views.sql`, `analytical_queries.sql` |
| `tests/` | Math + invariant + integrity tests (all must pass) |
| `powerbi/` | Implementation package: data dictionary, model, DAX, 6-page build guide |
| `docs/` | Architecture, methodology, results, limitations, viva Q&A |
| `report/` | Academic report (.docx) + presentation outline |

## Power BI

No `.pbix` is shipped (it cannot be produced reproducibly here); `powerbi/`
contains the complete implementation package — data dictionary, star-schema
model, 20+ DAX measures, and a 6-page step-by-step build guide (~2 hours in
Power BI Desktop).

## Tech stack

Python (pandas, NumPy, SciPy), SQLite + SQL (CTEs, window functions, recursive
BOM explosion), Power BI (implementation package), Monte Carlo simulation.
