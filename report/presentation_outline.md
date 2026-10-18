# Viva Presentation Outline — Major Project
**Risk-Aware Safety-Stock Optimization and Material Shortage Decision Support System for Engineered-Component Manufacturing**
R. Shashank, B.M.S. College of Engineering — October 2026
*(All data synthetic; every number in this outline is computed from the project pipeline, none invented.)*

---

## Slide 1 — Title
- Full project title + "Synthetic Case Study" subtitle
- R. Shashank, Dept. of Industrial Engineering & Management, BMSCE, Oct 2026
- One framing line: a data-driven answer to how safety stocks should be set for intermittent engineered-component demand

## Slide 2 — The Problem (industry pain)
- Plant profile: thousands of raw/bought-out SKUs, intermittent lumpy demand, variable supplier lead times
- Planners use rule-of-thumb safety stocks → two chronic failure modes: line stoppages (stockouts) and excess working capital (overstock)
- This project: set safety stocks from data + give planners early shortage warnings + prioritise supply risks

## Slide 3 — Research Questions & Aim
- RQ1 (core): does KDE-based safety-stock dimensioning beat the textbook normal approximation at matched service, for intermittent demand with stochastic lead times?
- RQ2: cost-vs-service Pareto; RQ3: demand vs lead-time variability; RQ4: robustness to cost assumptions; RQ5: behaviour under disruption
- Operational deliverable: shortage-detection engine + ISO-31000-style risk module + planner analytics package

## Slide 4 — Data Provenance (say this up front)
- EVERYTHING is synthetic: seeded generator, seed 42, 24 months (Oct 2024–Sep 2026)
- 40 products · 200 materials · 20 suppliers in 3 behaviour tiers · ~1,500 production orders · ~2,700 PO lines · ~3,200 receipts · ~14,500 inventory transactions · a labeled 6-week supplier disruption
- 10/10 automated integrity checks pass — the findings describe method behaviour under stated conditions, NOT real-world performance

## Slide 5 — System Architecture
- Single source of truth: config/project_config.yaml → generator → SQLite (17 tables)
- Two tracks: (a) read-only SQL views — inventory ledger (window functions), recursive-CTE BOM explosion, time-phased projected stock, shortage flags, supplier scorecards; (b) Python estimation + (R,S) Monte Carlo simulator + experiments pipeline
- Risk module scores 122 risks → risk_scores.csv; Power BI delivered as an implementation package (model + DAX + build guide)
- Guards: train-only estimation (nothing reads past 2026-03-31), explicit seeds, test period quarantined for backtest

## Slide 6 — Methods: Safety-Stock Models
- Baselines: B1 = 4 weeks-of-cover rule of thumb; B2 = normal approx, SS = z·σ_d·√(L̄+R) (fixed-LT, symmetric assumption); B3 = min-max
- Proposed: bootstrap lead-time demand — draw lead time from empirical LT distribution, sum a contiguous demand block of (LT+R) days (captures BOTH demand and LT stochasticity)
- P1 = empirical quantile SS = Q_SL(LTD) − mean(LTD); P2 = Gaussian KDE (Silverman bandwidth) quantile, floored at 0, negative draws clipped
- Policy: weekly (R,S), order up to S = mean LTD + SS

## Slide 7 — Headline Result: E1 (KDE vs Normal at 95% service)
- Paired over 30 stratified materials, 100 MC reps: fill rate 0.944 → 0.955 (p=0.013); CSL 0.961 → 0.965 (p=0.038)
- Total cost ₹755,419 → ₹639,117: **−₹116,302 per material (−15.4%), Wilcoxon p = 0.036**
- Confirmed on the 6-month held-out backtest (₹904,184 → ₹797,418)
- This is the headline — lead with it, then immediately give the caveats (next slide)

## Slide 8 — Honest Caveats (examiners respect this)
- The 95% CI on the cost difference is WIDE and crosses zero: [−296,188, +63,584] — a few expensive high-volume SKUs drive the mean
- KDE holds MORE average inventory (+₹189k, p=0.005): it buys service with stock, and wins on total cost only because the modeled stockout penalty exceeds extra holding cost
- Absolute cost levels are modeling choices (holding 22%, ordering ₹1,500, penalty 2.5×) — never quote as real savings

## Slide 9 — Supporting Experiments (E2–E4)
- E2 Pareto: KDE dominates normal at every matched service level; note "more service is cheaper" is parameter-driven (penalty dominates holding), and min-max's 30-day buffer is arbitrary
- E3 lead-time ablation: removing LT stochasticity changes cost by only 2.4% — demand variability dominates; a genuine negative-ish result
- E4 sensitivity: KDE beats normal 9/9 across holding (15/22/30%) × penalty (1.5×/2.5×/4.0×) — the ranking is robust even though levels move

## Slide 10 — E5 Disruption + Risk Module
- Severe disruption (2.5× lead times, 1.2× demand): every policy degrades badly; KDE still leads (fill 0.754 vs 0.719 normal) but no rule fully insures the shock
- The message: disruption response needs mitigation actions (expediting, alternate sourcing), not bigger buffers — that's what the risk module is for
- Risk module: ISO-31000 lifecycle, data-computed features (OTD%, lateness, rejection rate, single-source flag, days of cover); ordinal 1–5×1–5 scales are a project-defined prioritization convention, never calibrated probabilities; expected loss only where n≥20 events

## Slide 11 — Limitations & Future Work
- All synthetic; generator choices shape results (reported via sensitivity, not hidden); no capacity constraints; independent SKUs; backordering assumption; cost params modeled not measured; KDE bandwidth Silverman (spot-checked, not exhaustively tuned)
- No ERP integration, no auto PO execution — decision support only; single plant, single echelon
- Future: calibrate costs from real plant data; planner-in-the-loop evaluation; autocorrelated/seasonal demand; multi-echelon positioning (Simchi-Levi & Zhao)

## Slide 12 — Conclusion & Contribution
- Built: integrated, reproducible DSS — shortage engine, risk module, KDE safety-stock optimizer, simulator, Power BI package — all runnable, all assumptions documented
- Found: KDE-based dimensioning cuts modeled total cost −15.4% vs the textbook normal rule at matched 95% service (p=0.036), robust 9/9, confirmed on holdout — honestly qualified by the wide CI and the penalty-dependence
- Takeaway: a disciplined, simulation-validated method comparison is the real contribution — and knowing exactly where it stops being true is part of it

---

## Anticipated Viva Questions (with one-line answers)

1. **Why KDE over the normal assumption?** Intermittent demand gives skewed, lumpy lead-time demand; normal forces symmetry and understates tail quantiles; KDE estimates the actual shape. It's also an active research question (2025 arXiv preprint).
2. **Isn't your result circular — you generated the data?** Demand archetypes follow the documented Syntetos/Boylan intermittency taxonomy, not hand-tuned shapes; the comparison is method-vs-method on identical streams (paired design); holdout backtest + sensitivity confirm it's not a one-seed artifact. The claim is precisely scoped to the stated conditions.
3. **Your cost CI crosses zero — is the E1 finding real?** The mean effect is significant (p=0.036) but heterogeneous across SKUs; I'm reporting exactly that. The honest claim is dominance under stated conditions, not a universal law.
4. **Why does KDE hold more inventory yet cost less?** It buys service with stock; total cost still falls because the modeled 2.5× stockout penalty exceeds the extra holding cost — under a lower penalty the ranking narrows (see E4).
5. **Why (R,S) and not (s,S)?** Periodic review matches planner cadence and ERP buckets; (s,S) is the continuous-review extension (future work).
6. **Fill rate vs cycle service level?** CSL = probability of no stockout per cycle; fill rate = fraction of demand met; I target fill rate (what production feels) and report both.
7. **E3 says lead-time variability barely matters — contradicting Simchi-Levi & Zhao?** Not a contradiction: their result holds when LT variability is large relative to demand variability. Here demand intermittency dominates; the KDE bootstrap already prices LT variability in, so removing it moves little.
8. **E2 says more service is cheaper — is that a general law?** No. It's parameter-driven: the 2.5× stockout penalty dominates holding cost. Change the penalty and it reverses. The apples-to-apples result is E1.
9. **Risk scores: are these real probabilities?** No — the 1–5×1–5 matrix is a project-defined ordinal prioritization convention, stated as such. Expected loss is only computed where n≥20 events support an empirical frequency.
10. **What breaks under real plant data?** Stationarity assumptions, unmodeled capacity constraints, lost-sales mix (not pure backordering), and the cost parameters — all listed as limitations; the first upgrade is calibrating costs from plant accounting data.
11. **Why no ML/deep learning in the estimation?** KDE is the right nonparametric tool for univariate distribution estimation; tree/DL models add nothing here and hurt interpretability. ML only where it beats a simpler baseline.
12. **How would this deploy at a real plant?** Export per-SKU (R,S) parameters from inventory_policy_params; planner reviews in Power BI; monthly re-estimation on the rolling window; pilot on C-class spares first; decision support only, never automatic PO execution.
