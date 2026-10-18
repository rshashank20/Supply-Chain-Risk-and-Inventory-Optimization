# Viva Questions & Answer Sketches

## Research question & contribution

**Q1. In one minute: what did you build and what did you find?**
A: An integrated decision-support system for engineered-component manufacturing:
a shortage-detection engine (SQL BOM explosion + projected inventory), a risk
prioritization module, and — the research core — a data-driven safety-stock
optimizer. Core finding: KDE-based safety-stock dimensioning beats the textbook
normal approximation on total modeled cost (−₹116k/material, p=0.036) at matched
95% service, robust across 9 cost-parameter combinations, and confirmed on a
6-month temporal holdout.

**Q2. Why KDE instead of just assuming normality?**
A: The normal SS formula, z·σ·√(L+R), assumes lead-time demand is symmetric and
fully described by mean/variance. Our intermittent SKUs have skewed, lumpy
lead-time demand (many small values, occasional spikes). KDE estimates the
actual shape from bootstrapped LTD samples instead of forcing symmetry — the
2025 arXiv preprint showed this is an active research question, and our
experiment tests it on tooling-component-like data.

**Q3. Your KDE "win" depends on how you generated the data. Isn't that circular?**
A: Fair challenge, and we address it three ways: (1) demand archetypes follow
the documented Syntetos/Boylan intermittency taxonomy, not hand-tuned shapes;
(2) the comparison is *method-vs-method under stated conditions* — both methods
see identical data; (3) E4 sensitivity and the holdout backtest confirm the
ranking isn't a one-seed artifact. The claim is precisely scoped, not universal.

## Methodology

**Q4. How did you prevent information leakage?**
A: Strict temporal split — all estimation uses the first 18 months; the last 6
months are never touched during estimation. Monte Carlo bootstraps demand from
train only. The backtest runs once on the holdout.

**Q5. Fill rate vs cycle service level — define both.**
A: Fill rate (β) = units satisfied immediately ÷ total units demanded. Cycle
service level (α) = fraction of replenishment (review) cycles with zero
stockouts. A policy can have high CSL but poor fill rate if the rare stockout
is huge — we report both.

**Q6. Why is min-max cheapest in your Pareto? Doesn't that kill your RQ?**
A: No — it's apples-to-oranges. Min-max implicitly targets ~98%+ service, and
with our 2.5× stockout penalty, higher service costs less in modeled terms.
The RQ compares methods at *matched* 95% targets (E1). The min-max result is
reported honestly as a parameter-driven finding, and its "30 days" buffer is
an arbitrary choice we flag.

## Data & validation

**Q7. How do you know your synthetic data isn't garbage?**
A: Ten automated integrity checks (FK integrity, date logic, quantity rules,
inventory sanity, BOM coverage) plus behavioral checks: the disruption window
shows 6.7 vs 0.7 days average lateness, single-sourcing exists (31 materials),
rejection rates differ by tier. The data behaves the way the business rules say
it should.

**Q8. Your risk scores (1–5 × 1–5) look arbitrary.**
A: They are project-defined ordinal cutoffs, documented as such — a
prioritization convention, not a probability model. We never multiply an
ordinal rating by currency; expected loss uses empirical frequencies only
where n≥20 supports them (10 risks correctly have blank expected loss).

## Industry relevance

**Q9. SAP does MRP already. What's your contribution?**
A: Three things SAP doesn't give a student project: (1) the validated
KDE-vs-normal research comparison with CIs; (2) the integrated risk→mitigation
loop with traceable evidence per recommendation; (3) transparent baseline-vs-
proposed policy comparison under identical simulated scenarios — reproducible
from a seed.

**Q10. Would you deploy this at a real plant?**
A: Not directly — the cost parameters need calibration from real accounting
data, and the risk dashboard needs planner-in-the-loop validation. What
transfers is the *method*: estimation → simulation validation → policy
comparison, all reproducible. That's the honest deployment story in
`docs/limitations.md`.
