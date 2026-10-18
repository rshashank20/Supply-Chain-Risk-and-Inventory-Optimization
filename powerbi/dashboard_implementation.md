# Dashboard Implementation Guide — Shortage DSS Report

> **Honest disclosure:** a real `.pbix` file **cannot be created here** — Power BI Desktop must run on a machine to build it. This guide gives exact visuals, fields, measures and click-by-click steps so the student can build the full 6-page report in **~2 hours**. **All data is synthetic** (seed 42, Spectrum Tool Engineers case study). Label every page footer: *"Synthetic case-study data — not a real company."*

**Global setup (do once):** build the model per `model_relationships.md`, paste the measures from `dax_measures.md`, then add a **report-level date slicer** (`dim_date[date]`, "Between" style) and shared slicers on each page: `materials[category]`, `materials[criticality]`, `suppliers[region]`. Sync slicers across pages (View → Sync slicers).

---

## Page 1 — Executive Risk Overview
**Purpose:** one-screen answer to "are we going to run out of material, and what is it costing us?"

**Visuals:**
1. **KPI cards (row of 5):** `[Stockout Materials]` · `[Open High Risks]` · `[Total Event Loss]` (format INR, 2 decimals → use "₹ #,##0") · `[On-Hand Inventory Value]` · `[Single-Source Materials]`
2. **Line chart — risk exposure trend:** Axis `dim_date[date]` (month level), Values `[Total Event Loss]`; add `[Rolling 4-Week Receipts]` as secondary line only if story needs it (keep 1).
3. **Bar chart — loss by event type:** Axis `risk_events[event_type]`, Values `[Total Event Loss]`.
4. **Matrix — criticality × category:** Rows `materials[criticality]`, Columns `materials[category]`, Values `[Stockout Materials]`, `[On-Hand Inventory Value]`; conditional-format Stockout Materials red when > 0.
5. **Text box:** 2-line narrative: current stockout count, top loss driver.

**Filters/slicers:** global date slicer + category/criticality/region.
**Drill-through targets:** Page 2 (from stockout card), Page 5 (from risk card).
**Build steps:**
1. Insert 5 Card visuals; drop in the 5 measures; set number formats (INR for money).
2. Insert Line chart → X-axis `dim_date[date]` → right-click axis → change to Month hierarchy level; Y = `[Total Event Loss]`.
3. Insert Bar chart → Y-axis `risk_events[event_type]` → X-axis `[Total Event Loss]` → sort descending.
4. Insert Matrix → configure rows/columns/values → Format → Cell elements → background color scale on `[Stockout Materials]`.
5. Insert Text box → type the 2-line summary; add footer "Synthetic case-study data".

## Page 2 — Material Availability & Shortage Monitoring
**Purpose:** the daily control tower — which materials are short, how long stock lasts, where consumption is spiking.

**Visuals:**
1. **KPI cards:** `[Usable Stock]` is per-material so instead card `[Stockout Materials]`, `[MTD Material Issues]`, `[MoM Issues Change %]` (format % with ▲/▼ conditional icon).
2. **Table — material watchlist:** Columns `materials[material_id]`, `materials[category]`, `materials[unit]`, `[Usable Stock]`, `materials[safety_stock]` (baseline), `[Days of Cover]`, `[Avg Daily Consumption]`, `[Stockout Flag]`; conditional-format Days of Cover < 7 red, 7–14 amber.
3. **Line chart — receipts vs issues:** Axis `dim_date[date]` (week), Values `[Rolling 4-Week Receipts]` and `[MTD Material Issues]` (use two line values; MTD resets monthly — acceptable for the demo).
4. **Bar chart — consumption by category:** Axis `materials[category]`, Values `[MTD Material Issues]`.

**Filters/slicers:** global + `materials[criticality]` emphasized (default select `A` via slicer to show critical first).
**Drill-through targets:** drill-through from a material row → Page 4 (procurement view filtered to that material).
**Build steps:**
1. Cards first (3).
2. Table visual → add columns in order → Format → Conditional formatting on `[Days of Cover]` (rules: <7 red background, <14 amber) and icon set on `[Stockout Flag]`.
3. Line chart → X-axis date at Week level → add both measures → Format → turn on data labels off, markers on.
4. Bar chart → sort by `[MTD Material Issues]` descending.
5. Right-click Page 4 in Pages pane → set up drill-through later (see Page 4 note); test by right-clicking a material row → Drill through.

## Page 3 — Supplier Performance & Risk
**Purpose:** supplier scorecard — who delivers late, who ships bad quality, where is supply concentrated.

**Visuals:**
1. **Table — supplier scorecard:** Columns `suppliers[supplier_id]`, `suppliers[region]`, `suppliers[capacity_band]`, `[OTD %]` (format %), `[Avg Lateness (days)]`, `[Rejection Rate]` (format %), `[Late Receipts]`; conditional-format OTD % < 90% red, Rejection Rate > 3% red.
2. **Gauge — overall OTD:** Value `[OTD %]`, target 95%.
3. **Bar chart — lateness by supplier:** Axis `suppliers[supplier_id]`, Values `[Avg Lateness (days)]`, sorted descending.
4. **Line chart — rejection-rate trend:** Axis `dim_date[date]` (month), Values `[Rejection Rate]`.
5. **Card:** `[Single-Source Materials]` with caption "materials with one qualified supplier".

**Filters/slicers:** global + `suppliers[region]`.
**Drill-through targets:** right-click a supplier row → drill to a hidden **detail page** (Page 3b, "Supplier Detail") showing that supplier's receipts table (`goods_receipts[receipt_id]`, `[receipt_date]`, promised date, days late) and its materials.
**Build steps:**
1. Build scorecard table → apply the two conditional formats.
2. Gauge → set Minimum 0, Maximum 1, Target 0.95.
3. Bar + line charts as specified.
4. Create hidden detail page: New page → add Table with receipt-level columns + a DAX column `Days Late = DATEDIFF(RELATED(purchase_order_lines[promised_date]), goods_receipts[receipt_date], DAY)` → Page settings → *Allow drill-through* → drag `suppliers[supplier_id]` into Drill-through filters → hide page (right-click → Hide page).
5. Test drill-through from scorecard.

## Page 4 — Inventory & Procurement Optimization
**Purpose:** connect inventory position to procurement action — what to order, from whom, at what lead time.

**Visuals:**
1. **KPI cards:** `[On-Hand Inventory Value]`, `[Rolling 4-Week Receipts]`, `[MTD Material Issues]`.
2. **Matrix — inventory value by category × criticality:** Rows `materials[category]`, Columns `materials[criticality]`, Values `[On-Hand Inventory Value]`, `[Usable Stock]`.
3. **Table — reorder review:** Columns `materials[material_id]`, `[Usable Stock]`, `[Reorder Point (Policy)]`, `[Days of Cover]`, primary supplier (`supplier_material` filtered `is_primary = 1` → show `suppliers[supplier_id]` via a calculated column on materials), `supplier_material[lead_time_days]`; filter table to `[Usable Stock] <= [Reorder Point (Policy)]` via visual-level filter.
4. **Bar chart — lead-time distribution:** Axis `suppliers[supplier_id]` (or bins of `lead_time_days`), Values average `supplier_material[lead_time_days]`.
5. **Line chart — receipts vs issues trend** (reuse Page-2 style, month grain).

**Filters/slicers:** global + `inventory_policy_params[policy_name]` slicer (drives `[Reorder Point (Policy)]`).
**Drill-through targets:** this page is the drill-through target from Page 2 (receives `materials[material_id]` filter).
**Build steps:**
1. Add policy slicer first (it changes the reorder table).
2. Cards → matrix → reorder table (set visual-level filter: `[Usable Stock]` ≤ `[Reorder Point (Policy)]` — use "is less than or equal to" with the measure; Power BI allows measure-based visual filters via *Filters on this visual*).
3. Primary-supplier display: create calculated column on `materials`: `Primary Supplier = CALCULATE(MAX(supplier_material[supplier_id]), supplier_material[is_primary]=1)` — works through the material relationships.
4. Enable drill-through: Page 4 → *Allow drill-through* → drag `materials[material_id]` to Drill-through filters → Back button appears automatically.

## Page 5 — Risk Register & Mitigation Actions
**Purpose:** the risk workflow — what can go wrong, what already did, and whether mitigations are on track.

**Visuals:**
1. **KPI cards:** `[Open High Risks]`, `[Avg Priority Score]`, `[Total Event Loss]`, `[Overdue Mitigation Actions]`.
2. **Scatter — risk matrix:** X `risk_register[likelihood]`, Y `risk_register[impact]`, Details `risk_register[risk_id]`, Size `[Total Event Loss]` (or fixed), Color by `risk_register[category]`; add quadrant lines at 3.
3. **Table — risk register:** `risk_id`, `category`, `material_id`, `supplier_id`, `likelihood`, `impact`, priority score (calculated column `likelihood*impact`), `owner_role`, `status`.
4. **Table — mitigation actions:** `action_id`, `risk_id`, `action_type`, `estimated_cost` (INR), `due_date`, `status`, `outcome`; conditional-format `due_date` red when overdue and status ≠ complete.
5. **Bar chart — event loss by event type:** Axis `risk_events[event_type]`, Values `[Total Event Loss]`.

**Filters/slicers:** global + `risk_register[status]`, `risk_register[category]`.
**Drill-through targets:** none (terminal page); drill-through *from* Page 1 lands here filtered by risk.
**Build steps:**
1. Cards → scatter (Format → X/Y axis start at 1, end at 5) → register table → actions table → loss bar.
2. Overdue formatting: select `due_date` column → Conditional formatting → Background color → Rules: if `due_date` < TODAY() and `status` ≠ "complete" → red (implement as a DAX flag column `Is Overdue = IF(mitigation_actions[status]<>"complete" && mitigation_actions[due_date] < TODAY(), 1, 0)` and format on that).
3. Verify the 12 risks / 20 events / 15 actions counts match the dictionary.

## Page 6 — Policy & Scenario Comparison
**Purpose:** the experiment payoff — which safety-stock policy wins, and how policies behave under disruption scenarios.

**Visuals:**
1. **Slicer:** `inventory_policy_params[policy_name]` (single-select: `weeks_of_cover_4`, `normal_ss_95`, `minmax`).
2. **KPI cards:** `[Policy Safety Stock]`, `[Policy Holding Cost (INR/yr)]`, `[Cost Delta vs Baseline]` (red if positive, green if negative via conditional font color).
3. **Clustered bar — SS by policy:** Axis `materials[category]`, Legend `inventory_policy_params[policy_name]`, Values `[Policy Safety Stock]` — remove the policy slicer interaction for this visual (Format → Edit interactions → none) so all three policies show.
4. **Table — policy parameters:** `materials[material_id]`, `policy_name`, `[Policy Safety Stock]`, `[Reorder Point (Policy)]`, `inventory_policy_params[order_up_to]`.
5. **Scenario cards/table:** `scenario_config[name]`, `description`, `demand_mult`, `lt_mult`, `defect_mult` — documents the six disruption scenarios (S0–S5).
6. **⚠️ Simulation results placeholder:** `simulation_results` is **empty in this DB copy** (0 rows). After running the Monte Carlo simulator, add: clustered bar of average `fill_rate` by `policy_name` × `scenario_id`, and a line of `total_cost` distribution (use a box-style visual or error bars on mean ± std). Wire the visual now against the table; it will populate when the simulator output is loaded.

**Filters/slicers:** policy slicer (page-level); scenario table is descriptive.
**Drill-through targets:** none.
**Build steps:**
1. Add policy slicer (single select) → cards → bar (disable slicer interaction on the bar) → parameter table → scenario table.
2. For the simulator visuals: add the visuals, bind fields, leave them showing blank with a text box: "Populate after running the Monte Carlo simulator (2000 replications × 6 scenarios × 3 policies)".
3. Sanity check: `[Policy Safety Stock]` totals ≈ 818,980 (weeks_of_cover_4), ≈ 1,002,740 (normal_ss_95 / minmax — currently identical, see dictionary note) across all 200 materials.

---

## Data Import — connecting Power BI Desktop to the SQLite DB

### Option A — Direct SQLite connection via ODBC (recommended)
1. Install the SQLite ODBC driver: download **sqliteodbc** (http://www.ch-werner.de/sqliteodbc/) → install the 64-bit version (match Power BI Desktop 64-bit).
2. Windows: *ODBC Data Sources (64-bit)* → *System DSN* → *Add* → **SQLite3 ODBC Driver** → Data Source Name: `ShortageDSS` → Database Name: browse to `data/processed/shortage_dss.db` → OK.
3. Power BI Desktop → *Get Data* → **ODBC** → DSN `ShortageDSS` → select all 17 tables → *Transform Data*.
4. In Power Query: for every `*_date` TEXT column set type **Date** (select column → Data Type → Date). Verify `dim_date[date]` is Date type.
5. *Close & Apply*. Then build relationships per `model_relationships.md` (or let auto-detect run and correct it against the relationship list).

### Option B — CSV export (no driver install)
1. Export each table: `sqlite3 data/processed/shortage_dss.db ".headers on" ".mode csv" ".output <table>.csv" "SELECT * FROM <table>;"` — or run `src/` export script if present.
2. Power BI → *Get Data* → **Text/CSV** → load the 17 CSVs → set date columns to Date type in Power Query.
3. Same relationship build as Option A.

### Refresh note
The DB is a static synthetic snapshot. Re-running the generator (`src/`) overwrites the DB — then *Refresh* in Power BI re-imports everything; no model changes needed.

---

## KPI Reconciliation Checklist

Verify each KPI in Power BI against its source with the SQL below (run with `sqlite3 data/processed/shortage_dss.db`). All figures are **synthetic**.

| # | KPI (Power BI measure) | Source SQL (expected logic) | Python/config source |
|---|---|---|---|
| 1 | `[Usable Stock]` (per material, no date filter) | `SELECT material_id, SUM(signed_qty) FROM inventory_transactions GROUP BY material_id;` | generator `src/` (seed 42) |
| 2 | `[On-Hand Inventory Value]` | `SELECT SUM(t.s) FROM (SELECT material_id, SUM(signed_qty) s FROM inventory_transactions GROUP BY material_id) t JOIN materials m ON t.material_id=m.material_id;` → Σ usable × unit_cost | `materials.unit_cost` (lognormal around category base cost) |
| 3 | `[OTD %]` | `SELECT AVG(gr.receipt_date <= pol.promised_date) FROM goods_receipts gr JOIN purchase_order_lines pol ON gr.po_line_id=pol.po_line_id;` | supplier tier `late_p` in `project_config.yaml` |
| 4 | `[Avg Lateness (days)]` | `SELECT AVG(julianday(gr.receipt_date)-julianday(pol.promised_date)) FROM goods_receipts gr JOIN purchase_order_lines pol ON gr.po_line_id=pol.po_line_id WHERE gr.receipt_date > pol.promised_date;` | tier `late_max` (3/10/25 days) |
| 5 | `[Rejection Rate]` | `SELECT SUM(rejected_qty)/(SUM(received_qty)+SUM(rejected_qty)) FROM goods_receipts;` | tier `defect` (0.005/0.02/0.05) |
| 6 | `[Open High Risks]` | `SELECT COUNT(*) FROM risk_register WHERE status IN ('open','mitigating') AND likelihood*impact >= 15;` | generator risk module |
| 7 | `[Total Event Loss]` | `SELECT SUM(estimated_loss) FROM risk_events;` | generator (incl. 2025-03-01→2025-04-15 disruption of supplier index 6) |
| 8 | `[Policy Safety Stock]` per policy | `SELECT policy_name, SUM(safety_stock) FROM inventory_policy_params GROUP BY policy_name;` → expect ≈ 818,980 (weeks_of_cover_4), ≈ 1,002,740 (normal_ss_95, minmax) | planning engine / optimizer output |
| 9 | `[Cost Delta vs Baseline]` | Holding-cost rate **0.22** from `project_config.yaml` → `safety_stock.holding_cost_rate_annual` | config, not data |
| 10 | `[Rolling 4-Week Receipts]` | `SELECT SUM(received_qty) FROM goods_receipts WHERE receipt_date BETWEEN date('2026-09-30','-28 days') AND '2026-09-30';` (adjust end date) | generator receipts |
| 11 | `[Single-Source Materials]` | `SELECT COUNT(*) FROM (SELECT material_id FROM supplier_material GROUP BY material_id HAVING COUNT(*)=1);` | generator sourcing assignments |
| 12 | Row counts | `products 40, materials 200, suppliers 20, production_orders 1500, purchase_orders 480, po_lines 2677, receipts 3231, inv_txn 14450, risks 12, events 20, actions 15, policy_params 600, scenarios 6, sim_results 0` | — |

**Sign-off rule:** a KPI is "reconciled" only when the Power BI value (with all slicers cleared, date slicer covering the full 2024-10-01→2026-09-30 range) matches the SQL result. Document any mismatch before the viva — the likeliest cause is a date column left as Text in Power Query.
