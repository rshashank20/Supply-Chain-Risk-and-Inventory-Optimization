# Data Dictionary — Risk-Aware Safety-Stock Optimization & Material Shortage DSS

> **Honest disclosure (read first):** A real `.pbix` file **cannot be created here** — building a Power BI report requires Power BI Desktop running on a machine. This package (4 files) is a complete build kit: data dictionary, star-schema model design, ready-to-paste DAX measures, and click-by-click dashboard instructions. A student can build the full report in Power BI Desktop in **~2 hours** following these files.
>
> **All data in the source database is synthetic** — generated from `config/project_config.yaml` (seed 42) as a case study for Spectrum Tool Engineers Pvt. Ltd. Nothing in this package represents a real company, real suppliers, or real financial figures.
>
> Database: `data/processed/shortage_dss.db` (SQLite) · Case context: Spectrum Tool Engineers Pvt. Ltd. (**synthetic case study**) · History window: **2024-10-01 → 2026-09-30** (730 days) · Config seed: 42.

---

## 1. DIMENSIONS (slow-changing masters)

### dim_date
**Grain:** one row per calendar day, 2024-10-01 → 2026-09-30 (730 rows).
**Role:** role-playing date table — one shared table joined multiple times to every fact date (order date, promised date, receipt date, transaction date, release date, required date, event date, due date).

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `date` | TEXT (YYYY-MM-DD) | date | PK. Calendar day. |
| `year` | INTEGER | — | Calendar year (2024/2025/2026). |
| `month` | INTEGER | 1–12 | Month number. |
| `quarter` | INTEGER | 1–4 | Calendar quarter. |
| `week` | INTEGER | 1–52 | ISO-ish week number of year. |
| `is_weekend` | INTEGER | 0/1 | 1 = Saturday/Sunday. |

PK: `date`. FK: referenced by facts via TEXT date columns (joins must be set up as date-type relationships in Power BI).

### products
**Grain:** one row per finished product (40 rows).

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `product_id` | TEXT | — | PK. e.g. `FG-001`. |
| `product_family` | TEXT | — | Family: `cutting_tools`, `press_parts`, `mould_bases`, `jigs_fixtures`. |
| `unit` | TEXT | unit | Finished-goods unit (`pcs`). |
| `active_flag` | INTEGER | 0/1 | 1 = product still manufactured. |

### materials
**Grain:** one row per raw material / component (200 rows). **Core dimension for the shortage analysis.**

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `material_id` | TEXT | — | PK. e.g. `MAT-0001`. |
| `category` | TEXT | — | Material class: `alloy_steel`, `tool_steel`, `fasteners`, `carbide_inserts`, `abrasives`, `polymers`, `electronics`, `lubricants`. |
| `unit` | TEXT | unit | Stocking unit: `kg`, `pcs`, `ltr`. |
| `unit_cost` | REAL | INR per unit | Latest unit cost — drives inventory value and holding cost. |
| `criticality` | TEXT | — | ABC-style criticality: `A` (critical), `B`, `C`. |
| `safety_stock` | REAL | stocking units | Baseline (current) safety-stock quantity used in operations. |

### suppliers
**Grain:** one row per supplier (20 rows).

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `supplier_id` | TEXT | — | PK. e.g. `SUP-001`. |
| `region` | TEXT | — | Supplier region: `Karnataka`, `Maharashtra`, `Tamil Nadu`, `Delhi NCR`, `Gujarat`. |
| `status` | TEXT | — | `active` (all 20 currently active). |
| `capacity_band` | TEXT | — | Capacity tier: `A`/`B`/`C` (A = largest). |

### supplier_material
**Grain:** one row per (supplier, material) sourcing assignment (446 rows). **Bridge/quasi-dimension** — carries the primary-source flag and lead time used by the shortage engine.

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `supplier_id` | TEXT | — | PK part. FK → `suppliers.supplier_id`. |
| `material_id` | TEXT | — | PK part. FK → `materials.material_id`. |
| `lead_time_days` | INTEGER | days | Standard procurement lead time for this assignment. |
| `unit_price` | REAL | INR per unit | Purchase price from this supplier. |
| `moq` | REAL | stocking units | Minimum order quantity. |
| `max_capacity` | REAL | stocking units | Max quantity per order this supplier can deliver. |
| `is_primary` | INTEGER | 0/1 | 1 = primary (default) source for the material. |

### bom_lines
**Grain:** one row per (product, material) BOM line (336 rows).

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `bom_line_id` | INTEGER | — | PK. |
| `product_id` | TEXT | — | FK → `products.product_id`. |
| `material_id` | TEXT | — | FK → `materials.material_id`. |
| `qty_per_product` | REAL | stocking units per finished unit | Usage quantity per finished product. |
| `scrap_allowance` | REAL | fraction (e.g. 0.038) | Scrap factor applied when computing gross material requirement. |
| `effective_from` | TEXT | date | BOM validity start (all 2024-10-01). |
| `effective_to` | TEXT | date | BOM validity end (NULL = still current). |

---

## 2. FACTS (transactional / event)

### inventory_transactions
**Grain:** one row per inventory movement (14,450 rows). **Inventory fact.**

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `transaction_id` | INTEGER | — | PK. |
| `material_id` | TEXT | — | FK → `materials.material_id`. |
| `transaction_date` | TEXT | date | Date of the movement. Join → `dim_date[date]`. |
| `type` | TEXT | — | `OPENING` (opening balance, 200 rows), `RECEIPT` (goods receipt, 3,231), `ISSUE` (consumption to production, 10,899), `ADJUSTMENT` (cycle-count corrections, 120). |
| `signed_qty` | REAL | stocking units | Signed quantity. Convention observed in data: OPENING and RECEIPT positive, ISSUE negative, ADJUSTMENT signed ±. |
| `reference_id` | TEXT | — | Source reference (`OPENING-BAL`, receipt or order reference). |

### purchase_orders
**Grain:** one row per PO header (480 rows). **Procurement fact header.**

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `po_id` | TEXT | — | PK. e.g. `POH-00001`. |
| `supplier_id` | TEXT | — | FK → `suppliers.supplier_id`. |
| `order_date` | TEXT | date | PO creation date. Join → `dim_date[date]`. |
| `status` | TEXT | — | `planned`, `in_progress`, `completed`, `closed`. |
| `currency_code` | TEXT | — | `INR` throughout. |

### purchase_order_lines
**Grain:** one row per PO line (2,677 rows). **Procurement fact line.**

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `po_line_id` | TEXT | — | PK. e.g. `POL-000001`. |
| `po_id` | TEXT | — | FK → `purchase_orders.po_id`. |
| `material_id` | TEXT | — | FK → `materials.material_id`. |
| `order_qty` | REAL | stocking units | Quantity ordered. |
| `promised_date` | TEXT | date | Supplier-promised delivery date — denominator of OTD %. Join → `dim_date[date]`. |
| `unit_price` | REAL | INR per unit | Price locked on the PO line. |

### goods_receipts
**Grain:** one row per goods receipt (3,231 rows). **Procurement receipt fact.** One line can have multiple receipts (partial receipts).

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `receipt_id` | INTEGER | — | PK. |
| `po_line_id` | TEXT | — | FK → `purchase_order_lines.po_line_id`. |
| `receipt_date` | TEXT | date | Actual goods-receipt (GRN) date. Join → `dim_date[date]`. |
| `received_qty` | REAL | stocking units | Quantity accepted into stock (gross). |
| `rejected_qty` | REAL | stocking units | Quantity rejected at inspection (drives rejection-rate KPI). |

### production_orders
**Grain:** one row per production order (1,500 rows). **Demand-side fact** — drives material requirement via BOM explosion.

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `production_order_id` | TEXT | — | PK. e.g. `PO-00001`. |
| `product_id` | TEXT | — | FK → `products.product_id`. |
| `planned_qty` | REAL | finished units | Quantity planned to produce. |
| `release_date` | TEXT | date | Date released to the shop floor. Join → `dim_date[date]`. |
| `required_date` | TEXT | date | Date the finished product must be available (service commitment). Join → `dim_date[date]`. |
| `priority` | TEXT | — | `low` / `normal` / `high`. |
| `status` | TEXT | — | `planned`, `in_progress`, `completed`, `closed`. |

### risk_register
**Grain:** one row per identified supply risk (12 rows). **Risk dimension-fact.**

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `risk_id` | INTEGER | — | PK. |
| `category` | TEXT | — | `single_source`, `supplier_delivery`, `quality`, `material_availability`. |
| `material_id` | TEXT | — | FK → `materials.material_id` (nullable). |
| `supplier_id` | TEXT | — | FK → `suppliers.supplier_id` (nullable). |
| `likelihood` | INTEGER | 1–5 | Likelihood score. |
| `impact` | INTEGER | 1–5 | Impact score. |
| `owner_role` | TEXT | — | `operations_manager`, `production_planner`, `inventory_controller`, `procurement_analyst`. |
| `status` | TEXT | — | `open`, `mitigating`, `closed`. |

### risk_events
**Grain:** one row per realized risk occurrence (20 rows). **Risk event fact.** Date range 2025-01-03 → 2026-08-22.

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `event_id` | INTEGER | — | PK. |
| `risk_id` | INTEGER | — | FK → `risk_register.risk_id`. |
| `event_date` | TEXT | date | Date the event occurred. Join → `dim_date[date]`. |
| `event_type` | TEXT | — | `quality_hold`, `late_delivery`, `partial_shipment`, `demand_spike`. |
| `severity` | INTEGER | 1–5 | Event severity score. |
| `estimated_loss` | REAL | INR | Estimated financial loss of the event. |
| `disruption_days` | INTEGER | days | Supply-disruption duration (0 if none). |

### mitigation_actions
**Grain:** one row per mitigation action (15 rows).

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `action_id` | INTEGER | — | PK. |
| `risk_id` | INTEGER | — | FK → `risk_register.risk_id`. |
| `action_type` | TEXT | — | `quality_audit`, `alternate_source`, `expedite_po`, `safety_stock_raise`, `reprioritize_orders`. |
| `estimated_cost` | REAL | INR | Estimated cost of executing the action. |
| `due_date` | TEXT | date | Commitment date for completion. Join → `dim_date[date]`. |
| `status` | TEXT | — | `proposed`, `in_progress`, `complete`. |
| `outcome` | TEXT | — | Result note (e.g. `partial_relief`, `pending`). |

---

## 3. ANALYTICS / EXPERIMENT OUTPUT TABLES

### inventory_policy_params
**Grain:** one row per (material, policy) (600 = 200 materials × 3 policies). **Policy-comparison fact (output of the planning engine).**

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `policy_id` | INTEGER | — | PK. |
| `material_id` | TEXT | — | FK → `materials.material_id`. |
| `policy_name` | TEXT | — | Policy variant: `weeks_of_cover_4` (rule-of-thumb: 4 weeks of cover), `normal_ss_95` (normal-distribution SS at 95% service level), `minmax` (min–max reorder policy at 95% SL). |
| `service_level` | REAL | fraction | Target cycle service level (0.95 for all rows). |
| `safety_stock` | REAL | stocking units | Recommended safety stock under this policy. **Note (observed in data):** `normal_ss_95` and `minmax` currently return identical safety-stock values (avg ≈ 5,014 units); `weeks_of_cover_4` averages ≈ 4,095. Re-run the optimizer if the distinction matters for the viva. |
| `reorder_point` | REAL | stocking units | Reorder point under this policy. |
| `order_up_to` | REAL | stocking units | Order-up-to level under this policy. |
| `review_weeks` | REAL | weeks | Review period (1.0). |
| `source` | TEXT | — | Provenance flag (`baseline`). |

### scenario_config
**Grain:** one row per disruption scenario (6 rows). **Scenario dimension.**

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `scenario_id` | TEXT | — | PK. `S0_normal`, `S1_lt_disrupt`, `S2_demand_surge`, `S3_quality`, `S4_critical_var`, `S5_combined`. |
| `name` | TEXT | — | Display name, e.g. `S1 Lt Disrupt`. |
| `demand_mult` | REAL | multiplier | Demand shock multiplier (1.0–1.6). |
| `lt_mult` | REAL | multiplier | Lead-time shock multiplier (1.0–2.5). |
| `defect_mult` | REAL | multiplier | Defect-rate shock multiplier (1.0–4.0). |
| `description` | TEXT | — | Plain-English scenario description. |

### simulation_results
**Grain:** one row per (scenario, policy, Monte Carlo replication). **⚠️ CURRENTLY EMPTY (0 rows)** — the simulator has not been run/populated in this database copy. Populate before building Page 6, or wire it later.

| Column | Type | Unit | Business meaning |
|---|---|---|---|
| `result_id` | INTEGER | — | PK. |
| `scenario_id` | TEXT | — | FK → `scenario_config.scenario_id`. |
| `policy_name` | TEXT | — | Policy variant tested (same values as `inventory_policy_params.policy_name`). |
| `replication` | INTEGER | — | Monte Carlo replication number (1…2000 per config). |
| `fill_rate` | REAL | fraction | Simulated unit fill rate. |
| `cycle_service_level` | REAL | fraction | Simulated probability of no stockout in a cycle. |
| `stockout_count` | INTEGER | count | Number of stockout episodes in the replication. |
| `avg_inventory_value` | REAL | INR | Average on-hand inventory value. |
| `total_cost` | REAL | INR | Total cost (holding + ordering + stockout penalty) for the replication. |

---

## 4. FACT vs DIMENSION SUMMARY

| Table | Type | Grain |
|---|---|---|
| `dim_date` | Dimension (role-playing) | Day |
| `products` | Dimension | Product |
| `materials` | Dimension | Material |
| `suppliers` | Dimension | Supplier |
| `supplier_material` | Bridge / quasi-dimension | Supplier × Material |
| `bom_lines` | Dimension-like (many-to-many bridge) | Product × Material |
| `inventory_transactions` | **Fact** | Movement |
| `purchase_orders` | **Fact** (header) | PO |
| `purchase_order_lines` | **Fact** | PO line |
| `goods_receipts` | **Fact** | Receipt |
| `production_orders` | **Fact** | Production order |
| `risk_register` | Dimension-fact | Risk |
| `risk_events` | **Fact** | Risk event |
| `mitigation_actions` | **Fact** (workflow) | Action |
| `inventory_policy_params` | **Fact** (engine output) | Material × Policy |
| `scenario_config` | Dimension | Scenario |
| `simulation_results` | **Fact** (simulator output) | Scenario × Policy × Replication — *empty, populate before use* |

> **Synthetic-data reminder:** every value above — supplier tiers, lead times, the 6-week disruption of one supplier (2025-03-01 → 2025-04-15, config `disruption.supplier_index: 6`), quality and demand shocks — was generated by the project generator. Label dashboards "Synthetic case-study data".
