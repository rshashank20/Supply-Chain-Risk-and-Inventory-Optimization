-- ============================================================================
-- analytical_queries.sql — operational questions for the Shortage DSS
-- Project : Risk-Aware Safety-Stock Optimization and Material Shortage DSS
-- DATA    : ALL data is SYNTHETIC (seeded generator, seed 42). Nothing here
--           represents a real company, real suppliers, or real transactions.
-- USE     : sqlite3 data/processed/shortage_dss.db < database/analytical_queries.sql
--           Requires database/views.sql to be applied first (several queries
--           read the v_* views). Each query is standalone and ends with ";".
-- PLANNING ANCHOR: "today" = MAX(dim_date.date) = end of the 24-month history
--           window (2026-09-30). Forward-looking queries are relative to it.
-- ============================================================================

-- Q1: Which materials are at greatest stockout risk over the next 4 weeks?
-- Ranks materials by their planning-date position (final projected week):
-- first those already below safety stock (biggest buffer gap first), then by
-- weeks of cover at recent consumption rates. A buyer works this list top
-- down: expedite, transfer, or raise an emergency PO.
-- Reads: v_shortage_flags (planning-date week), v_inventory_ledger not needed.
SELECT s.material_id,
       s.category,
       s.criticality,
       ROUND(s.projected_stock, 1)      AS on_hand_projected,
       s.safety_stock,
       s.shortage_qty,
       ROUND(c.avg_wk_issue, 1)         AS avg_weekly_issue_12w,
       CASE WHEN c.avg_wk_issue > 0
            THEN ROUND(s.projected_stock / c.avg_wk_issue, 1) END
                                       AS weeks_of_cover
FROM v_shortage_flags s
JOIN (SELECT MAX(week_end) AS w FROM v_shortage_flags) m ON m.w = s.week_end
LEFT JOIN (
    SELECT material_id,
           SUM(CASE WHEN type = 'ISSUE' THEN -signed_qty ELSE 0 END) / 12.0
               AS avg_wk_issue
    FROM inventory_transactions
    WHERE transaction_date >
          date((SELECT MAX(transaction_date) FROM inventory_transactions),
               '-84 days')
    GROUP BY material_id
) c ON c.material_id = s.material_id
ORDER BY s.shortage_qty DESC, weeks_of_cover ASC
LIMIT 10;

-- Q2: Supplier OTD ranking — who delivers on time, and is lateness getting
-- worse? Scorecard per supplier (from v_supplier_performance) plus the
-- late-delivery rate in each of the last 3 months, so a buyer can see whether
-- a low OTD is a one-off or a deteriorating trend before escalating.
SELECT p.supplier_id,
       p.region,
       p.capacity_band,
       p.n_receipts,
       p.otd_pct,
       p.avg_lateness_days,
       p.rejection_rate_pct,
       RANK() OVER (ORDER BY p.otd_pct DESC) AS otd_rank,
       COALESCE(t.late_pct_m1, 'n/a') || '% | '
           || COALESCE(t.late_pct_m2, 'n/a') || '% | '
           || COALESCE(t.late_pct_m3, 'n/a') || '%'
           AS late_pct_trend_jul_aug_sep
FROM v_supplier_performance p
LEFT JOIN (
    SELECT po.supplier_id,
           ROUND(100.0
                 * SUM(CASE WHEN substr(g.receipt_date, 1, 7) = '2026-07'
                             AND g.receipt_date > pol.promised_date
                            THEN 1 ELSE 0 END)
                 / NULLIF(SUM(CASE WHEN substr(g.receipt_date, 1, 7) = '2026-07'
                                   THEN 1 ELSE 0 END), 0), 1) AS late_pct_m1,
           ROUND(100.0
                 * SUM(CASE WHEN substr(g.receipt_date, 1, 7) = '2026-08'
                             AND g.receipt_date > pol.promised_date
                            THEN 1 ELSE 0 END)
                 / NULLIF(SUM(CASE WHEN substr(g.receipt_date, 1, 7) = '2026-08'
                                   THEN 1 ELSE 0 END), 0), 1) AS late_pct_m2,
           ROUND(100.0
                 * SUM(CASE WHEN substr(g.receipt_date, 1, 7) = '2026-09'
                             AND g.receipt_date > pol.promised_date
                            THEN 1 ELSE 0 END)
                 / NULLIF(SUM(CASE WHEN substr(g.receipt_date, 1, 7) = '2026-09'
                                   THEN 1 ELSE 0 END), 0), 1) AS late_pct_m3
    FROM goods_receipts g
    JOIN purchase_order_lines pol ON pol.po_line_id = g.po_line_id
    JOIN purchase_orders po ON po.po_id = pol.po_id
    WHERE g.receipt_date >= '2026-07-01'
    GROUP BY po.supplier_id
) t ON t.supplier_id = p.supplier_id
ORDER BY p.otd_pct DESC;

-- Q3: Which PO lines are due in the next 14 days with no receipt yet?
-- The daily expediting list: promised inside the 14-day window but nothing
-- received. As of the planning anchor this returns zero rows — itself a
-- finding: no inbound pipeline is due in the first two weeks, so any new
-- demand must be covered from on-hand stock or emergency buys.
-- Reads: v_po_status (anchor-relative aging).
SELECT po_line_id,
       po_id,
       supplier_id,
       material_id,
       promised_date,
       order_qty,
       received_qty,
       aging_bucket
FROM v_po_status
WHERE promised_date >  (SELECT MAX(date) FROM dim_date)
  AND promised_date <= date((SELECT MAX(date) FROM dim_date), '+14 days')
  AND received_qty = 0
ORDER BY promised_date;

-- Q4: Which A-criticality materials are single-sourced?
-- The intersection of "cannot afford a stockout" and "one supplier failure
-- away from one". Each row is a dual-sourcing / safety-stock candidate for
-- the risk review. Shows the incumbent's lead time and the current position
-- vs safety stock.
SELECT m.material_id,
       m.category,
       m.unit_cost,
       sm.supplier_id,
       s.region,
       sm.lead_time_days,
       sm.unit_price,
       m.safety_stock,
       ROUND(l.running_balance, 1) AS on_hand_now
FROM materials m
JOIN supplier_material sm ON sm.material_id = m.material_id
JOIN suppliers s ON s.supplier_id = sm.supplier_id
LEFT JOIN (
    SELECT material_id, MAX(transaction_date) AS d
    FROM v_inventory_ledger GROUP BY material_id
) e ON e.material_id = m.material_id
LEFT JOIN v_inventory_ledger l
  ON l.material_id = e.material_id AND l.transaction_date = e.d
WHERE m.criticality = 'A'
  AND m.material_id IN (
      SELECT material_id FROM supplier_material
      GROUP BY material_id HAVING COUNT(*) = 1
  )
ORDER BY m.unit_cost DESC;

-- Q5: What is the on-hand inventory value by ABC (criticality) class?
-- Ending balance per material valued at standard unit cost, grouped by the
-- criticality class. Tells finance where working capital sits and whether
-- the "A" items that deserve tight control actually hold proportionate value.
-- (Two materials show negative value — data-quality flags for cycle count.)
SELECT m.criticality AS abc_class,
       COUNT(*) AS n_materials,
       ROUND(SUM(l.running_balance * m.unit_cost), 0) AS inventory_value,
       ROUND(100.0 * SUM(l.running_balance * m.unit_cost) /
             SUM(SUM(l.running_balance * m.unit_cost)) OVER (), 1)
           AS pct_of_total_value,
       ROUND(AVG(l.running_balance * m.unit_cost), 0) AS avg_value_per_material
FROM materials m
JOIN (
    SELECT material_id, MAX(transaction_date) AS d
    FROM v_inventory_ledger GROUP BY material_id
) e ON e.material_id = m.material_id
JOIN v_inventory_ledger l
  ON l.material_id = e.material_id AND l.transaction_date = e.d
GROUP BY m.criticality
ORDER BY inventory_value DESC;

-- Q6: Which receipts had >5% rejection, and what did it cost?
-- QC failures that shrank usable supply: rejected share, rejected value at
-- PO unit price, supplier and material. Recurring names here feed the
-- supplier quality review (and the quality scenario S3 in the simulator).
SELECT g.receipt_id,
       g.receipt_date,
       po.supplier_id,
       pol.material_id,
       m.category,
       g.received_qty,
       g.rejected_qty,
       ROUND(100.0 * g.rejected_qty / g.received_qty, 1) AS rejected_pct,
       ROUND(g.rejected_qty * pol.unit_price, 0) AS rejected_value
FROM goods_receipts g
JOIN purchase_order_lines pol ON pol.po_line_id = g.po_line_id
JOIN purchase_orders po ON po.po_id = pol.po_id
JOIN materials m ON m.material_id = pol.material_id
WHERE g.rejected_qty / g.received_qty > 0.05
ORDER BY rejected_value DESC
LIMIT 20;

-- Q7: Which open production orders are exposed to a material shortage?
-- Joins each planned/in-progress order's exploded requirements to the
-- shortage flags in the order's required week. An exposed order cannot be
-- completed as planned — the planner must pull it in, substitute, or
-- reschedule before the required date arrives.
-- NOTE: as of the planning anchor this returns zero rows — a genuine
-- finding, not an empty query: none of the 43 open orders' materials sit
-- below safety stock in their required weeks. Re-run any time; rows here
-- are the must-act reschedule list.
SELECT gr.production_order_id,
       gr.product_id,
       gr.required_date,
       gr.priority,
       COUNT(DISTINCT f.material_id) AS n_short_materials,
       GROUP_CONCAT(DISTINCT f.material_id) AS short_materials,
       ROUND(SUM(f.shortage_qty), 1) AS total_shortage_qty
FROM v_gross_requirements gr
JOIN v_shortage_flags f
  ON f.material_id = gr.material_id
 AND f.week_end = date(
        date(gr.required_date,
             '-' || ((CAST(strftime('%w', gr.required_date) AS INTEGER) + 6) % 7)
             || ' days'),
        '+6 days')
WHERE gr.status IN ('planned', 'in_progress')
GROUP BY gr.production_order_id, gr.product_id, gr.required_date, gr.priority
ORDER BY total_shortage_qty DESC;

-- Q8: What does the lead-time distribution look like per supplier tier?
-- p25/p50/p75/p90 of actualised lead times (PO order date -> first receipt)
-- by behaviour tier. Tiers are not stored in the DB; they follow the
-- generator's deterministic assignment (config shares 30/45/25 over
-- sequential supplier ids): SUP-001..006 reliable, SUP-007..015 standard,
-- SUP-016..020 risky. Wide p90-p50 spreads mark the tiers whose variability
-- the safety-stock model must absorb.
WITH tiered AS (
    SELECT po.supplier_id,
           CASE WHEN po.supplier_id BETWEEN 'SUP-001' AND 'SUP-006' THEN 'reliable'
                WHEN po.supplier_id BETWEEN 'SUP-007' AND 'SUP-015' THEN 'standard'
                ELSE 'risky' END AS tier,
           julianday(MIN(g.receipt_date)) - julianday(po.order_date) AS lt_days
    FROM purchase_orders po
    JOIN purchase_order_lines pol ON pol.po_id = po.po_id
    JOIN goods_receipts g ON g.po_line_id = pol.po_line_id
    GROUP BY po.supplier_id, pol.po_line_id, po.order_date
),
ranked AS (
    SELECT tier, lt_days,
           ROW_NUMBER() OVER (PARTITION BY tier ORDER BY lt_days) AS rn,
           COUNT(*) OVER (PARTITION BY tier) AS n
    FROM tiered
)
SELECT tier,
       COUNT(*) AS n_lines,
       ROUND(AVG(lt_days), 1) AS mean_lt,
       ROUND(MAX(CASE WHEN rn = CAST(0.25 * (n - 1) + 1 AS INTEGER)
                     THEN lt_days END), 1) AS p25_lt,
       ROUND(MAX(CASE WHEN rn = CAST(0.50 * (n - 1) + 1 AS INTEGER)
                     THEN lt_days END), 1) AS p50_lt,
       ROUND(MAX(CASE WHEN rn = CAST(0.75 * (n - 1) + 1 AS INTEGER)
                     THEN lt_days END), 1) AS p75_lt,
       ROUND(MAX(CASE WHEN rn = CAST(0.90 * (n - 1) + 1 AS INTEGER)
                     THEN lt_days END), 1) AS p90_lt
FROM ranked
GROUP BY tier
ORDER BY p50_lt;

-- Q9: What are the open risks, ranked by priority?
-- risk_register items still open/mitigating, priority = likelihood x impact,
-- with realised-event counts, cumulative estimated loss, and the latest
-- mitigation action status. The top rows are the risk-review agenda.
SELECT r.risk_id,
       r.category,
       r.material_id,
       r.supplier_id,
       r.likelihood,
       r.impact,
       r.likelihood * r.impact AS priority_score,
       r.owner_role,
       r.status,
       COUNT(DISTINCT e.event_id) AS n_events,
       ROUND(COALESCE(SUM(e.estimated_loss), 0), 0) AS total_estimated_loss,
       (SELECT ma.status FROM mitigation_actions ma
         WHERE ma.risk_id = r.risk_id
         ORDER BY ma.due_date DESC LIMIT 1) AS latest_mitigation_status
FROM risk_register r
LEFT JOIN risk_events e ON e.risk_id = r.risk_id
WHERE r.status IN ('open', 'mitigating')
GROUP BY r.risk_id, r.category, r.material_id, r.supplier_id,
         r.likelihood, r.impact, r.owner_role, r.status
ORDER BY priority_score DESC, total_estimated_loss DESC;

-- Q10: Which disruption scenarios are configured, and are results ready?
-- Lists the simulator's scenario definitions (demand/lead-time/defect
-- multipliers) with the number of Monte-Carlo result rows stored per
-- scenario. Zero rows everywhere is expected: simulation_results is populated
-- by the experiment stage, not the generator.
SELECT s.scenario_id,
       s.name,
       s.demand_mult,
       s.lt_mult,
       s.defect_mult,
       s.description,
       COUNT(r.result_id) AS n_result_rows
FROM scenario_config s
LEFT JOIN simulation_results r ON r.scenario_id = s.scenario_id
GROUP BY s.scenario_id, s.name, s.demand_mult, s.lt_mult,
         s.defect_mult, s.description
ORDER BY s.scenario_id;

-- Q11: How much working capital does each safety-stock policy tie up?
-- Total safety-stock value (safety_stock x unit_cost) per baseline policy
-- from the pivoted comparison view. The cheapest policy is not automatically
-- the best — the simulator trades this holding cost against stockout cost —
-- but this is the "price tag" each policy carries into that trade-off.
SELECT 'weeks_of_cover_4' AS policy,
       ROUND(SUM(ss_weeks_of_cover_4 * unit_cost), 0) AS total_ss_value,
       ROUND(AVG(ss_weeks_of_cover_4), 1) AS avg_ss_qty
FROM v_policy_comparison
UNION ALL
SELECT 'normal_ss_95',
       ROUND(SUM(ss_normal_ss_95 * unit_cost), 0),
       ROUND(AVG(ss_normal_ss_95), 1)
FROM v_policy_comparison
UNION ALL
SELECT 'minmax',
       ROUND(SUM(ss_minmax * unit_cost), 0),
       ROUND(AVG(ss_minmax), 1)
FROM v_policy_comparison
ORDER BY total_ss_value;

-- Q12: Which materials are chronically exposed to shortage?
-- Counts, over the full 24-month history, how many weeks each material spent
-- below safety stock, with the worst single-week gap and the exposure window.
-- Chronic names (many flagged weeks) need structural fixes — dual sourcing,
-- lead-time reduction, or a higher service level — not one-off expediting.
SELECT material_id,
       category,
       criticality,
       COUNT(*) AS n_flagged_weeks,
       ROUND(MAX(shortage_qty), 1) AS worst_shortage_qty,
       MIN(week_end) AS first_flagged_week,
       MAX(week_end) AS last_flagged_week
FROM v_shortage_flags
GROUP BY material_id, category, criticality
ORDER BY n_flagged_weeks DESC, worst_shortage_qty DESC
LIMIT 15;
