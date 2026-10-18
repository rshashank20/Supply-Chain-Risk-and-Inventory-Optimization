# System Architecture

```
┌─────────────────────┐
│ config/project_     │  All assumptions: seed, tiers, scenarios, cost params
│ config.yaml         │  (single source of truth for reproducibility)
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ src/data_generation │  Seeded generator → 17 tables
│ generate_data.py    │  Business rules: tier lateness, partial receipts,
└────────┬────────────┘  rejections, disruption window, single-sourcing
         ▼
┌─────────────────────┐
│ data/processed/     │  shortage_dss.db (SQLite)
│ shortage_dss.db     │  40 products · 200 materials · 20 suppliers
└────────┬────────────┘  24 months history
         │
    ┌────┴───────────────────────────────┐
    ▼                                    ▼
┌──────────────────┐          ┌──────────────────────┐
│ database/views   │          │ inventory_optimization│
│ .sql             │          │ safety_stock.py       │
│                  │          │ (train only)          │
│ v_inventory_     │          │ baselines + KDE/emp.  │
│  ledger          │          │ → inventory_policy_   │
│ v_gross_require- │          │    params (optimized) │
│  ments (BOM      │          └──────────┬───────────┘
│  recursive CTE)  │                     ▼
│ v_projected_     │          ┌──────────────────────┐
│  stock           │          │ simulation/          │
│ v_shortage_flags │          │ simulator.py         │
│ v_supplier_perf  │          │ (R,S) Monte Carlo +  │
└────────┬─────────┘          │ scenario engine      │
         │                    │ → simulation_results │
         │                    └──────────┬───────────┘
         │                               ▼
         │                    ┌──────────────────────┐
         │                    │ experiments.py       │
         │                    │ E1–E5 → experiment_   │
         │                    │ results.json         │
         │                    └──────────────────────┘
         │
    ┌────┴──────────────────────────────┐
    ▼                                   ▼
┌──────────────────┐          ┌──────────────────────┐
│ risk_management/ │          │ powerbi/             │
│ risk_engine.py   │          │ implementation pkg   │
│ 122 risks scored │          │ data dict · model ·  │
│ → risk_scores.csv│          │ DAX · 6-page guide   │
└──────────────────┘          └──────────────────────┘
```

## Module responsibilities

| Module | Owns | Must not |
|---|---|---|
| data_generation | Synthetic records + integrity checks | Touch policy/simulation tables' results |
| database/views | Reusable SQL analytics (read-only) | Write to DB |
| inventory_optimization | Estimation on TRAIN only; writes optimized policies | Read test-period data |
| simulation | (R,S) simulator; scenario runner; writes simulation_results | Change policies |
| risk_management | Risk features → scores → CSV | Invent probabilities |
| experiments | E1–E5 orchestration; JSON results | — |
| tests | Math/invariant/integrity tests | — |

## Data flow guarantees

1. **Temporal discipline:** nothing in `inventory_optimization` reads past
   2026-03-31. The test period is quarantined for backtest/simulation.
2. **Reproducibility:** every stochastic step takes an explicit seed;
   `run_project.py --seed 7` regenerates an independent variant.
3. **Single config:** cost parameters, scenario multipliers, and service
   levels live in `project_config.yaml`, not in code.
