# Limitations & Assumptions

Honest boundaries of this project. Every limitation below is a potential viva
question — each has a prepared answer in `docs/viva_questions.md`.

## Data limitations

1. **All data is synthetic.** Demand patterns, supplier behaviours, and costs
   are modeled assumptions, not observations. Results demonstrate *method
   behaviour under stated conditions*, not real-world performance.
2. **The generator's choices shape the results.** E.g., the KDE-vs-normal
   finding depends on the demand intermittency encoded in generation
   (Bernoulli×lognormal archetypes). A different DGP could narrow or reverse
   the gap — reported via sensitivity analysis, not hidden.
3. **No capacity constraints.** The MRP/shortage engine assumes infinite
   production capacity (standard MRP limitation, Heizer ch.14). Labour and
   machine bottlenecks are out of scope.
4. **Independent SKUs.** No substitution or commonality effects beyond shared
   BOM components; no multi-item joint replenishment.

## Modeling assumptions

5. **Cost parameters are modeled, not measured.** Holding 22%/yr, ordering
   ₹1,500, stockout penalty 2.5× unit cost. Absolute cost rankings (e.g.,
   min-max cheapest in E2) follow from these choices — see E4 sensitivity.
6. **Backordering.** Unfilled demand backorders rather than being lost; real
   plants see a mix.
7. **Stationarity within buckets.** Demand distributions are assumed stationary
   over the 18-month train window; structural breaks would require re-estimation.
8. **Risk scores are ordinal.** The 1–5×1–5 matrix is a project-defined
   prioritization convention, not a calibrated probability model. Expected loss
   is computed only where empirical frequencies (n≥20) support it.
9. **KDE bandwidth.** Silverman rule-of-thumb; cross-validation was spot-checked
   but not exhaustively tuned. Near-constant demand falls back to the empirical
   quantile (documented guard against singular covariance).

## Scope exclusions

10. **No real ERP integration**, no real-time data, no automatic PO execution —
    decision support only.
11. **No .pbix shipped.** Power BI is delivered as a complete implementation
    package (model + DAX + build guide), honestly labeled as such.
12. **Single plant, single echelon** (suppliers → plant stores). Multi-echelon
    network optimization is future work.

## What would strengthen this work

- Calibrating cost parameters from real plant accounting data.
- Planner-in-the-loop evaluation of the risk dashboard (does it change decisions?).
- Extending the KDE comparison to autocorrelated demand and seasonal patterns.
- Multi-echelon safety-stock positioning (Simchi-Levi & Zhao framework).
