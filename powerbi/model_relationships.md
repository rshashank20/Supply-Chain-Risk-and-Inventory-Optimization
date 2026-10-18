# Model Relationships — Star Schema for the Shortage DSS

> All data is **synthetic** (seed 42, Spectrum Tool Engineers case study). No `.pbix` is created here — build the model below in Power BI Desktop (~30 min of the ~2 h build).

## 1. Star Schema Overview

**Fact tables:** `inventory_transactions`, `purchase_order_lines`, `purchase_orders`, `goods_receipts`, `production_orders`, `risk_register` (dimension-fact), `risk_events`, `mitigation_actions`, `inventory_policy_params`, `simulation_results` (empty — wire after running the simulator).

**Dimension tables:** `dim_date` (role-playing), `materials`, `suppliers`, `products`, `supplier_material` (bridge), `bom_lines` (BOM bridge), `scenario_config`.

**ASCII schema diagram** (every line = one-to-many, single direction `Dim → Fact` unless noted):

```
                    ┌───────────────┐
                    │   dim_date    │── role-playing (see §3)
                    └───────────────┘
          ┌────────────┐         ┌──────────────────┐
          │  materials ├─────────┤ inventory_policy_params │
          └────────────┘         └──────────────────┘
          │      │               ┌─────────────────────────┐
          │      └───────────────┤ inventory_transactions  │
          │                      └─────────────────────────┘
          │                      ┌──────────────────────┐
          └──────────────────────┤ purchase_order_lines │
          │                      └──────────────────────┘
   ┌──────────────┐              ┌────────────────┐
   │   suppliers  ├──────────────┤ purchase_orders│
   └──────────────┘              └────────────────┘
          │                      ┌────────────────┐
          └──────────────────────┤ goods_receipts │ (via po_line_id)
          │                      └────────────────┘
   ┌─────────────────┐           ┌──────────────────┐
   │supplier_material│(bridge)   │ production_orders│
   └─────────────────┘           └──────────────────┘
          │                              │
   ┌──────────────┐              ┌────────────┐
   │  risk_register├──────────────┤ risk_events│
   └──────────────┘              └────────────┘
          │
          └──────────────────┬──────────────────┐
                    ┌────────────────────┐  ┌─────────────┐
                    │ mitigation_actions │  │scenario_config├── simulation_results
                    └────────────────────┘  └─────────────┘

   ┌──────────┐        ┌───────────┐        ┌───────────┐
   │ products ├────────┤ bom_lines ├────────┤ materials │
   └──────────┘  (M:N bridge)     └───────────┘
```

## 2. Relationship List (build these in Model view)

Filter direction: **single** (`Dimension → Fact`) for all unless marked otherwise. `dim_date` relationships are **inactive** (dotted) except one default — all time-intelligence measures activate the right one with `USERELATIONSHIP()`.

| # | From (one) | To (many) | Join keys | Notes |
|---|---|---|---|---|
| 1 | `materials[material_id]` | `inventory_transactions[material_id]` | material_id | Single direction |
| 2 | `materials[material_id]` | `purchase_order_lines[material_id]` | material_id | |
| 3 | `materials[material_id]` | `inventory_policy_params[material_id]` | material_id | |
| 4 | `materials[material_id]` | `supplier_material[material_id]` | material_id | Single — dim side is material |
| 5 | `materials[material_id]` | `bom_lines[material_id]` | material_id | |
| 6 | `suppliers[supplier_id]` | `purchase_orders[supplier_id]` | supplier_id | |
| 7 | `suppliers[supplier_id]` | `supplier_material[supplier_id]` | supplier_id | |
| 8 | `purchase_orders[po_id]` | `purchase_order_lines[po_id]` | po_id | Header → lines |
| 9 | `purchase_order_lines[po_line_id]` | `goods_receipts[po_line_id]` | po_line_id | Cross-filter for OTD: single direction `purchase_order_lines → goods_receipts` |
| 10 | `products[product_id]` | `production_orders[product_id]` | product_id | |
| 11 | `products[product_id]` | `bom_lines[product_id]` | product_id | |
| 12 | `risk_register[risk_id]` | `risk_events[risk_id]` | risk_id | |
| 13 | `risk_register[risk_id]` | `mitigation_actions[risk_id]` | risk_id | |
| 14 | `materials[material_id]` | `risk_register[material_id]` | material_id | Nullable FK — use **single** direction |
| 15 | `suppliers[supplier_id]` | `risk_register[supplier_id]` | supplier_id | Nullable FK — single direction |
| 16 | `scenario_config[scenario_id]` | `simulation_results[scenario_id]` | scenario_id | Empty fact — wire now, populate later |

## 3. Role-Playing Date Handling (dim_date)

Import `dim_date` once as the table **`dim_date`**, then create **inactive** relationships for every fact date; keep exactly **one active** relationship (mark it active — the default for MTD/rolling logic). All others are activated inside measures via `USERELATIONSHIP()`.

| Relationship | Active? | Used by |
|---|---|---|
| `dim_date[date]` → `inventory_transactions[transaction_date]` | ✅ Active (default) | Days of Cover, MTD Issues, Usable Stock at end-of-period |
| `dim_date[date]` → `goods_receipts[receipt_date]` | Inactive | Rolling 4-week receipts, OTD %, Avg Lateness, Rejection Rate |
| `dim_date[date]` → `purchase_orders[order_date]` | Inactive | PO volume / spend by order month |
| `dim_date[date]` → `purchase_order_lines[promised_date]` | Inactive | Promised-delivery schedule, OTD denominator |
| `dim_date[date]` → `production_orders[release_date]` | Inactive | Demand-schedule charts |
| `dim_date[date]` → `production_orders[required_date]` | Inactive | At-risk production (material shortage vs required date) |
| `dim_date[date]` → `risk_events[event_date]` | Inactive | Event-loss timeline |
| `dim_date[date]` → `mitigation_actions[due_date]` | Inactive | Overdue actions |

> **Why:** `transaction_date` drives inventory position over time, so it is the natural active date. Everything else uses `USERELATIONSHIP()` in its measure (see `dax_measures.md`) — avoids ambiguous paths and keeps the calendar slicer working globally.

**Date table setup:** mark `dim_date` as the date table in Power BI (Model view → right-click `dim_date` → *Mark as date table* → choose `date`). Make sure all fact date columns are imported as **Date** type (SQLite TEXT → Power BI will import as Text; convert in Power Query: `Table.TransformColumnTypes(...,{{"receipt_date", type date}})` etc.).

## 4. Tables to Hide (hide from Report view)

Hide every **bridge/junction detail table** whose grain would confuse report authors, plus all raw key/date columns that aren't sliced on:

- **Hide entire tables:** `bom_lines`, `supplier_material` (slicing goes through `materials`/`suppliers`), `purchase_orders` header detail (slice via `suppliers` + PO measures on lines), `risk_register` *detail columns only*.
- **Hide columns (not tables):** `*_id` key columns on facts (`transaction_id`, `po_line_id`, `receipt_id`, `event_id`, `action_id`, `policy_id`, `result_id`), `reference_id`, `currency_code`, `effective_from/to`, `is_weekend` (keep only for weekday calculations), `dim_date[week]` if unused.
- **Keep visible:** all `dim_*` tables, fact tables themselves (needed for measures), `scenario_config[name, description]`, `inventory_policy_params[policy_name, service_level]`.

## 5. BOM Bridge Handling

`bom_lines` is a many-to-many bridge between `products` and `materials`. For production-driven material demand:
- Create the measure `[Required Material Qty]` (see `dax_measures.md`) that expands `production_orders[planned_qty] × bom_lines[qty_per_product] × (1 + scrap_allowance)`.
- If you need filter propagation `products → materials` through the BOM, set relationship #11 `products → bom_lines` single and #5 `bom_lines → materials` single (both single direction avoids ambiguity), and use `CROSSFILTER()` or `TREATAS()` in the measure.

## 6. Storage / Import Settings

- Import mode for all tables (data is small: largest fact is 14,450 rows).
- In Power Query, rename columns to business-friendly names optionally (keep originals — the DAX in this package uses the original DB column names).
- Add a calculated column on `dim_date`: `MonthName = FORMAT(dim_date[date],"mmm yyyy")` and `YearMonth = YEAR(dim_date[date])*100 + MONTH(dim_date[date])` for sorting.

> Reminder: every table, column and sample value in this design was read from the live synthetic SQLite database — no invented entities.
