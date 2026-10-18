-- ============================================================================
-- views.sql — reusable analytical views for the Shortage DSS
-- Project : Risk-Aware Safety-Stock Optimization and Material Shortage DSS
-- DATA    : ALL data is SYNTHETIC (seeded generator, seed 42). Nothing here
--           represents a real company, real suppliers, or real transactions.
-- USE     : sqlite3 data/processed/shortage_dss.db < database/views.sql
--           Idempotent (DROP VIEW IF EXISTS before each CREATE).
-- PLANNING ANCHOR: the dataset's 24-month history ends 2026-09-30; views that
--           need "today" use MAX(dim_date.date) instead of date('now').
-- ============================================================================

-- ----------------------------------------------------------------------------
-- v_inventory_ledger
-- Business purpose: the authoritative daily stock position per material.
-- Collapses all transactions to one row per material per day, then builds the
-- running on-hand balance with a window function. Every inventory KPI
-- (ending balance, weeks of cover, stock value) starts from this ledger.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_inventory_ledger;
CREATE VIEW v_inventory_ledger AS
WITH daily AS (
    SELECT material_id,
           transaction_date,
           SUM(CASE WHEN signed_qty > 0 THEN signed_qty ELSE 0 END) AS qty_in,
           SUM(CASE WHEN signed_qty < 0 THEN -signed_qty ELSE 0 END) AS qty_out,
           SUM(signed_qty) AS net_qty
    FROM inventory_transactions
    GROUP BY material_id, transaction_date
)
SELECT material_id,
       transaction_date,
       ROUND(qty_in, 2)  AS qty_in,
       ROUND(qty_out, 2) AS qty_out,
       ROUND(net_qty, 2) AS net_qty,
       ROUND(SUM(net_qty) OVER (
           PARTITION BY material_id
           ORDER BY transaction_date
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
       ), 2) AS running_balance
FROM daily;

-- ----------------------------------------------------------------------------
-- v_supplier_performance
-- Business purpose: one scorecard row per supplier for vendor management.
-- OTD% is receipt-level (a receipt is on time if it arrives on/before the
-- promised date); avg lateness is averaged over LATE receipts only; rejection
-- rate is QC-rejected qty / received qty; lead-time mean/sd come from the
-- first receipt of each PO line vs the PO order date (the actualised lead
-- time the planner experiences).
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_supplier_performance;
CREATE VIEW v_supplier_performance AS
WITH line_first AS (
    SELECT pol.po_line_id,
           po.supplier_id,
           po.order_date,
           pol.promised_date,
           MIN(g.receipt_date) AS first_receipt_date
    FROM purchase_order_lines pol
    JOIN purchase_orders po ON po.po_id = pol.po_id
    LEFT JOIN goods_receipts g ON g.po_line_id = pol.po_line_id
    GROUP BY pol.po_line_id, po.supplier_id, po.order_date, pol.promised_date
),
lead_times AS (
    SELECT supplier_id,
           julianday(first_receipt_date) - julianday(order_date) AS lt_days
    FROM line_first
    WHERE first_receipt_date IS NOT NULL
),
receipts AS (
    SELECT po.supplier_id,
           COUNT(*) AS n_receipts,
           SUM(CASE WHEN g.receipt_date <= pol.promised_date THEN 1 ELSE 0 END)
               AS n_ontime,
           AVG(CASE WHEN g.receipt_date > pol.promised_date
                    THEN julianday(g.receipt_date) - julianday(pol.promised_date)
               END) AS avg_lateness_days,
           SUM(g.rejected_qty) AS tot_rejected,
           SUM(g.received_qty) AS tot_received
    FROM goods_receipts g
    JOIN purchase_order_lines pol ON pol.po_line_id = g.po_line_id
    JOIN purchase_orders po ON po.po_id = pol.po_id
    GROUP BY po.supplier_id
)
SELECT s.supplier_id,
       s.region,
       s.status,
       s.capacity_band,
       (SELECT COUNT(*) FROM line_first lf WHERE lf.supplier_id = s.supplier_id)
           AS n_po_lines,
       r.n_receipts,
       ROUND(100.0 * r.n_ontime / NULLIF(r.n_receipts, 0), 2) AS otd_pct,
       ROUND(r.avg_lateness_days, 2) AS avg_lateness_days,
       ROUND(100.0 * r.tot_rejected / NULLIF(r.tot_received, 0), 2)
           AS rejection_rate_pct,
       ROUND(AVG(lt.lt_days), 2) AS lead_time_mean_days,
       ROUND(SQRT(MAX(AVG(lt.lt_days * lt.lt_days)
                      - AVG(lt.lt_days) * AVG(lt.lt_days), 0)), 2)
           AS lead_time_sd_days
FROM suppliers s
LEFT JOIN receipts r ON r.supplier_id = s.supplier_id
LEFT JOIN lead_times lt ON lt.supplier_id = s.supplier_id
GROUP BY s.supplier_id, s.region, s.status, s.capacity_band,
         r.n_receipts, r.n_ontime, r.avg_lateness_days,
         r.tot_rejected, r.tot_received;

-- ----------------------------------------------------------------------------
-- v_po_status
-- Business purpose: procurement aging dashboard — every PO line with its
-- receipt coverage, whether it is open, and how far past its promise date it
-- is. "Today" is the planning anchor (end of the history window), so aging is
-- stable and reproducible. Open lines (received < ordered) are the ones a
-- buyer must chase; overdue open lines are expedite candidates.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_po_status;
CREATE VIEW v_po_status AS
WITH anchor AS (
    SELECT MAX(date) AS today FROM dim_date
),
recv AS (
    SELECT pol.po_line_id,
           COUNT(g.receipt_id) AS n_receipts,
           COALESCE(SUM(g.received_qty), 0) AS received_qty,
           COALESCE(SUM(g.rejected_qty), 0) AS rejected_qty,
           MIN(g.receipt_date) AS first_receipt_date,
           MAX(g.receipt_date) AS last_receipt_date
    FROM purchase_order_lines pol
    LEFT JOIN goods_receipts g ON g.po_line_id = pol.po_line_id
    GROUP BY pol.po_line_id
)
SELECT pol.po_line_id,
       pol.po_id,
       po.supplier_id,
       pol.material_id,
       po.order_date,
       pol.promised_date,
       pol.order_qty,
       pol.unit_price,
       ROUND(pol.order_qty * pol.unit_price, 2) AS line_value,
       r.n_receipts,
       ROUND(r.received_qty, 2) AS received_qty,
       ROUND(r.rejected_qty, 2) AS rejected_qty,
       r.first_receipt_date,
       r.last_receipt_date,
       ROUND(100.0 * r.received_qty / NULLIF(pol.order_qty, 0), 2) AS received_pct,
       CASE WHEN r.received_qty >= pol.order_qty THEN 'fully_received'
            WHEN r.received_qty > 0 THEN 'partially_received'
            ELSE 'no_receipt'
       END AS receipt_status,
       -- days late vs promise: last receipt for received lines, anchor for open
       CAST(julianday(COALESCE(r.last_receipt_date, a.today))
            - julianday(pol.promised_date) AS INTEGER) AS days_vs_promise,
       CASE WHEN r.received_qty < pol.order_qty AND a.today > pol.promised_date
            THEN CAST(julianday(a.today) - julianday(pol.promised_date) AS INTEGER)
            ELSE 0
       END AS overdue_days_open,
       CASE
           WHEN r.received_qty >= pol.order_qty
                AND julianday(COALESCE(r.last_receipt_date, a.today))
                    <= julianday(pol.promised_date) THEN 'received_on_time'
           WHEN r.received_qty >= pol.order_qty THEN 'received_late'
           WHEN a.today <= pol.promised_date THEN 'open_not_due'
           WHEN julianday(a.today) - julianday(pol.promised_date) <= 7
               THEN 'open_overdue_1_7d'
           WHEN julianday(a.today) - julianday(pol.promised_date) <= 30
               THEN 'open_overdue_8_30d'
           ELSE 'open_overdue_31d_plus'
       END AS aging_bucket
FROM purchase_order_lines pol
JOIN purchase_orders po ON po.po_id = pol.po_id
CROSS JOIN anchor a
LEFT JOIN recv r ON r.po_line_id = pol.po_line_id;

-- ----------------------------------------------------------------------------
-- v_gross_requirements
-- Business purpose: the demand side of material planning. Explodes every
-- production order through the BOM (RECURSIVE CTE, depth-capped at 10 so the
-- view stays correct if the BOM ever gains sub-assembly levels; the current
-- BOM is single-level so recursion terminates at depth 1) into gross material
-- quantities per required date. Only BOM lines effective on the order's
-- required date are used; quantities include the scrap allowance.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_gross_requirements;
CREATE VIEW v_gross_requirements AS
WITH RECURSIVE bom_explosion(product_id, material_id, qty_per_unit,
                             eff_from, eff_to, lvl) AS (
    -- anchor: direct components of each product
    SELECT b.product_id,
           b.material_id,
           b.qty_per_product * (1.0 + COALESCE(b.scrap_allowance, 0)),
           b.effective_from,
           b.effective_to,
           1
    FROM bom_lines b
    UNION ALL
    -- recursive step: a "material" that is itself a manufactured product
    SELECT e.product_id,
           b.material_id,
           e.qty_per_unit * b.qty_per_product
               * (1.0 + COALESCE(b.scrap_allowance, 0)),
           b.effective_from,
           b.effective_to,
           e.lvl + 1
    FROM bom_explosion e
    JOIN bom_lines b ON b.product_id = e.material_id
    WHERE e.lvl < 10
)
SELECT po.production_order_id,
       po.product_id,
       po.required_date,
       po.status,
       po.priority,
       e.material_id,
       ROUND(SUM(po.planned_qty * e.qty_per_unit), 2) AS gross_qty
FROM production_orders po
JOIN bom_explosion e
  ON e.product_id = po.product_id
 AND e.eff_from <= po.required_date
 AND (e.eff_to IS NULL OR e.eff_to >= po.required_date)
GROUP BY po.production_order_id, po.product_id, po.required_date,
         po.status, po.priority, e.material_id;

-- ----------------------------------------------------------------------------
-- v_projected_stock
-- Business purpose: forward/backward stock projection per material per week —
-- the core of the shortage DSS. projected_stock(week) = opening balance
-- + cumulative ACCEPTED receipts (receipt_date <= week_end; QC-rejected qty
-- excluded, because rejected stock cannot be consumed) − cumulative gross
-- requirements (required_date <= week_end). Dips show when each material's
-- net position went thin; the final week is the planning-date position.
-- ----------------------------------------------------------------------------
-- ----------------------------------------------------------------------------
-- v_projected_stock
-- Business purpose: forward/backward stock projection per material per week —
-- the core of the shortage DSS. projected_stock(week) = opening balance
-- + cumulative ACCEPTED receipts (receipt_date <= week_end; QC-rejected qty
-- excluded, because rejected stock cannot be consumed) − cumulative gross
-- requirements (required_date <= week_end). Dips show when each material's
-- net position went thin; the final week is the planning-date position.
-- Weeks are ISO weeks (Monday..Sunday, week_end = Sunday). This SQLite build
-- lacks strftime('%V'), so the ISO-week Monday is derived arithmetically from
-- strftime('%w') (0=Sunday..6=Saturday): monday = d - ((w+6)%7) days.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_projected_stock;
CREATE VIEW v_projected_stock AS
WITH RECURSIVE
bounds(first_monday, last_monday) AS (
    SELECT
        date(MIN(d), '-' || ((CAST(strftime('%w', MIN(d)) AS INTEGER) + 6) % 7)
             || ' days'),
        date(MAX(d), '-' || ((CAST(strftime('%w', MAX(d)) AS INTEGER) + 6) % 7)
             || ' days')
    FROM (
        SELECT receipt_date AS d FROM goods_receipts
        UNION ALL
        SELECT required_date FROM production_orders
        UNION ALL
        SELECT transaction_date FROM inventory_transactions WHERE type = 'OPENING'
    )
),
weeks(monday) AS (
    SELECT first_monday FROM bounds
    UNION ALL
    SELECT date(w.monday, '+7 days')
    FROM weeks w, bounds b
    WHERE w.monday < b.last_monday
),
rec_week AS (
    -- accepted receipts bucketed to their ISO-week Monday
    SELECT pol.material_id,
           date(g.receipt_date,
                '-' || ((CAST(strftime('%w', g.receipt_date) AS INTEGER) + 6) % 7)
                || ' days') AS monday,
           SUM(g.received_qty - g.rejected_qty) AS accepted_qty
    FROM goods_receipts g
    JOIN purchase_order_lines pol ON pol.po_line_id = g.po_line_id
    GROUP BY pol.material_id, monday
),
req_week AS (
    -- gross requirements bucketed to their ISO-week Monday
    SELECT gr.material_id,
           date(gr.required_date,
                '-' || ((CAST(strftime('%w', gr.required_date) AS INTEGER) + 6) % 7)
                || ' days') AS monday,
           SUM(gr.gross_qty) AS gross_qty
    FROM v_gross_requirements gr
    GROUP BY gr.material_id, monday
),
grid AS (
    SELECT m.material_id, w.monday, date(w.monday, '+6 days') AS week_end
    FROM materials m
    CROSS JOIN weeks w
),
cum AS (
    SELECT g.material_id,
           g.week_end,
           COALESCE(SUM(r.accepted_qty) OVER wsum, 0) AS cum_accepted,
           COALESCE(SUM(q.gross_qty)  OVER wsum, 0) AS cum_gross
    FROM grid g
    LEFT JOIN rec_week r
      ON r.material_id = g.material_id AND r.monday = g.monday
    LEFT JOIN req_week q
      ON q.material_id = g.material_id AND q.monday = g.monday
    WINDOW wsum AS (PARTITION BY g.material_id ORDER BY g.monday)
),
opening AS (
    SELECT material_id, SUM(signed_qty) AS opening_qty
    FROM inventory_transactions
    WHERE type = 'OPENING'
    GROUP BY material_id
)
SELECT c.material_id,
       c.week_end,
       ROUND(COALESCE(o.opening_qty, 0) + c.cum_accepted - c.cum_gross, 2)
           AS projected_stock
FROM cum c
LEFT JOIN opening o ON o.material_id = c.material_id;
-- ----------------------------------------------------------------------------
-- v_shortage_flags
-- Business purpose: the DSS alert list — every material/week where the
-- projected usable stock falls below the material's safety stock, with the
-- shortage quantity (how much buffer is missing). Planners work top-down from
-- this: biggest shortage_qty and A-criticality first.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_shortage_flags;
CREATE VIEW v_shortage_flags AS
SELECT p.material_id,
       m.category,
       m.criticality,
       m.unit,
       p.week_end,
       p.projected_stock,
       m.safety_stock,
       ROUND(m.safety_stock - p.projected_stock, 2) AS shortage_qty
FROM v_projected_stock p
JOIN materials m ON m.material_id = p.material_id
WHERE p.projected_stock < m.safety_stock;

-- ----------------------------------------------------------------------------
-- v_policy_comparison
-- Business purpose: side-by-side comparison of the three baseline inventory
-- policies (weeks_of_cover_4, normal_ss_95, minmax) per material — safety
-- stock, reorder point and order-up-to level in one row. The safety-stock
-- optimizer writes competing policies here; this view is how planners and
-- the experiment harness compare them before picking a winner.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_policy_comparison;
CREATE VIEW v_policy_comparison AS
SELECT m.material_id,
       m.category,
       m.criticality,
       m.unit,
       m.unit_cost,
       MAX(CASE WHEN p.policy_name = 'weeks_of_cover_4'
                THEN p.safety_stock END)  AS ss_weeks_of_cover_4,
       MAX(CASE WHEN p.policy_name = 'weeks_of_cover_4'
                THEN p.reorder_point END) AS rop_weeks_of_cover_4,
       MAX(CASE WHEN p.policy_name = 'weeks_of_cover_4'
                THEN p.order_up_to END)   AS ou_weeks_of_cover_4,
       MAX(CASE WHEN p.policy_name = 'normal_ss_95'
                THEN p.safety_stock END)  AS ss_normal_ss_95,
       MAX(CASE WHEN p.policy_name = 'normal_ss_95'
                THEN p.reorder_point END) AS rop_normal_ss_95,
       MAX(CASE WHEN p.policy_name = 'normal_ss_95'
                THEN p.order_up_to END)   AS ou_normal_ss_95,
       MAX(CASE WHEN p.policy_name = 'minmax'
                THEN p.safety_stock END)  AS ss_minmax,
       MAX(CASE WHEN p.policy_name = 'minmax'
                THEN p.reorder_point END) AS rop_minmax,
       MAX(CASE WHEN p.policy_name = 'minmax'
                THEN p.order_up_to END)   AS ou_minmax
FROM materials m
LEFT JOIN inventory_policy_params p ON p.material_id = m.material_id
GROUP BY m.material_id, m.category, m.criticality, m.unit, m.unit_cost;
