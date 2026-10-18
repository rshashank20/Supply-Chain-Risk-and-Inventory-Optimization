# DAX Measures — Shortage DSS Model

> **Paste these into Power BI Desktop** (Model view → *New measure*). They are written against the model defined in `model_relationships.md` and the exact column names in `data_dictionary.md`. **All source data is synthetic** (seed 42, Spectrum Tool Engineers case study). No `.pbix` is created here.
>
> Convention: measure names use spaces (e.g. `[Usable Stock]`). All date comparisons assume fact date columns were converted to **Date** type in Power Query. `dim_date` is marked as the date table; its relationship to `inventory_transactions[transaction_date]` is the **active** one — every other date relationship is inactive and switched on with `USERELATIONSHIP()` inside the measure.

---

## A. Inventory measures

### 1. Usable Stock
```dax
Usable Stock =
VAR AsOfDate = MAX ( dim_date[date] )
RETURN
    CALCULATE (
        SUM ( inventory_transactions[signed_qty] ),
        FILTER ( ALL ( dim_date ), dim_date[date] <= AsOfDate )
    )
```
**Plain English:** total on-hand quantity of a material as of the last date in the current filter context (opening + receipts − issues ± adjustments). Respects the material/category slicers; ignores the date slicer except for the as-of point.
**Used in:** Page 2 — stock-by-material table, "materials below safety stock" card visuals, Days of Cover.

### 2. On-Hand Inventory Value
```dax
On-Hand Inventory Value =
SUMX (
    VALUES ( materials[material_id] ),
    [Usable Stock] * CALCULATE ( MAX ( materials[unit_cost] ) )
)
```
**Plain English:** rupee value of current on-hand stock (usable quantity × latest unit cost, INR).
**Used in:** Page 1 KPI card; Page 4 inventory-value-by-category bar.

### 3. Avg Daily Consumption
```dax
Avg Daily Consumption =
VAR EndDate = MAX ( dim_date[date] )
VAR StartDate = EndDate - 89
RETURN
    DIVIDE (
        - CALCULATE (
            SUM ( inventory_transactions[signed_qty] ),
            inventory_transactions[type] = "ISSUE",
            dim_date[date] >= StartDate,
            dim_date[date] <= EndDate
        ),
        90,
        0
    )
```
**Plain English:** average daily material consumption over the trailing 90 days (issues are stored negative, hence the minus sign). The denominator of Days of Cover.
**Used in:** Page 2 days-of-cover table.

### 4. Days of Cover
```dax
Days of Cover =
DIVIDE ( [Usable Stock], [Avg Daily Consumption], BLANK () )
```
**Plain English:** how many days current stock lasts at the recent consumption rate. BLANK when there is no consumption history.
**Used in:** Page 2 — days-of-cover matrix with conditional formatting (< 7 days = red).

### 5. Stockout Flag
```dax
Stockout Flag =
IF ( [Usable Stock] <= 0, 1, 0 )
```
**Plain English:** 1 if the material is stocked-out (zero or negative usable stock) in the current context, else 0.
**Used in:** Page 2 — conditional formatting / icon in material table; feeds Stockout Materials.

### 6. Stockout Materials
```dax
Stockout Materials =
COUNTROWS (
    FILTER ( VALUES ( materials[material_id] ), [Usable Stock] <= 0 )
)
```
**Plain English:** count of distinct materials currently stocked out.
**Used in:** Page 1 KPI card; Page 2 summary.

---

## B. Supplier measures

> OTD and lateness join each receipt to its PO line via `RELATED(purchase_order_lines[promised_date])` (relationship #9, single direction `purchase_order_lines → goods_receipts`).

### 7. OTD %
```dax
OTD % =
VAR TotalReceipts = COUNTROWS ( goods_receipts )
VAR OnTimeReceipts =
    COUNTROWS (
        FILTER (
            goods_receipts,
            goods_receipts[receipt_date] <= RELATED ( purchase_order_lines[promised_date] )
        )
    )
RETURN
    CALCULATE (
        DIVIDE ( OnTimeReceipts, TotalReceipts, 0 ),
        USERELATIONSHIP ( dim_date[date], goods_receipts[receipt_date] )
    )
```
**Plain English:** share of goods receipts arriving on or before the supplier-promised date. Date slicer applies to the receipt date.
**Used in:** Page 3 — supplier scorecard table, OTD gauge, trend line by month.

### 8. Avg Lateness (days)
```dax
Avg Lateness (days) =
CALCULATE (
    AVERAGEX (
        FILTER (
            goods_receipts,
            goods_receipts[receipt_date] > RELATED ( purchase_order_lines[promised_date] )
        ),
        DATEDIFF (
            RELATED ( purchase_order_lines[promised_date] ),
            goods_receipts[receipt_date],
            DAY
        )
    ),
    USERELATIONSHIP ( dim_date[date], goods_receipts[receipt_date] )
)
```
**Plain English:** average days late, computed only over late receipts (on-time receipts excluded).
**Used in:** Page 3 — supplier scorecard, lateness-by-supplier bar.

### 9. Rejection Rate
```dax
Rejection Rate =
VAR Rejected = SUM ( goods_receipts[rejected_qty] )
VAR Received = SUM ( goods_receipts[received_qty] )
RETURN
    CALCULATE (
        DIVIDE ( Rejected, Received + Rejected, 0 ),
        USERELATIONSHIP ( dim_date[date], goods_receipts[receipt_date] )
    )
```
**Plain English:** share of received quantity rejected at incoming inspection — the quality KPI per supplier/material.
**Used in:** Page 3 — quality tab of supplier scorecard, rejection-rate trend.

### 10. Late Receipts
```dax
Late Receipts =
CALCULATE (
    COUNTROWS (
        FILTER (
            goods_receipts,
            goods_receipts[receipt_date] > RELATED ( purchase_order_lines[promised_date] )
        )
    ),
    USERELATIONSHIP ( dim_date[date], goods_receipts[receipt_date] )
)
```
**Plain English:** count of receipts that arrived after the promised date.
**Used in:** Page 3 — late-delivery detail table (drill-through target from scorecard).

---

## C. Risk measures

### 11. Open High Risks
```dax
Open High Risks =
COUNTROWS (
    FILTER (
        risk_register,
        risk_register[status] IN { "open", "mitigating" }
            && risk_register[likelihood] * risk_register[impact] >= 15
    )
)
```
**Plain English:** number of risks still open or being mitigated whose priority score (likelihood × impact, max 25) is 15 or more.
**Used in:** Page 1 KPI card; Page 5 risk-matrix visual.

### 12. Avg Priority Score
```dax
Avg Priority Score =
AVERAGEX ( risk_register, risk_register[likelihood] * risk_register[impact] )
```
**Plain English:** average risk priority score across the register (likelihood × impact, 1–25 scale).
**Used in:** Page 5 — KPI card, priority distribution.

### 13. Total Event Loss
```dax
Total Event Loss =
CALCULATE (
    SUM ( risk_events[estimated_loss] ),
    USERELATIONSHIP ( dim_date[date], risk_events[event_date] )
)
```
**Plain English:** total estimated financial loss (INR) from realized risk events in the selected period.
**Used in:** Page 1 KPI card; Page 5 loss-by-event-type chart.

### 14. Overdue Mitigation Actions
```dax
Overdue Mitigation Actions =
COUNTROWS (
    FILTER (
        mitigation_actions,
        mitigation_actions[status] <> "complete"
            && mitigation_actions[due_date] < TODAY ()
    )
)
```
**Plain English:** mitigation actions past their due date that are not marked complete.
**Used in:** Page 5 — overdue-actions table with owner_role.

### 15. Single-Source Materials
```dax
Single-Source Materials =
COUNTROWS (
    FILTER (
        VALUES ( materials[material_id] ),
        CALCULATE ( COUNTROWS ( supplier_material ) ) = 1
    )
)
```
**Plain English:** materials with only one qualified supplier — the structural single-source exposure behind `single_source` risks.
**Used in:** Page 1 KPI; Page 3 supplier-concentration view.

---

## D. Policy comparison measures

> Slice these by `inventory_policy_params[policy_name]` (`weeks_of_cover_4`, `normal_ss_95`, `minmax`). Holding-cost rate 22%/yr comes from `config/project_config.yaml` (`safety_stock.holding_cost_rate_annual`).

### 16. Policy Safety Stock
```dax
Policy Safety Stock =
SUM ( inventory_policy_params[safety_stock] )
```
**Plain English:** total safety-stock quantity recommended under the selected policy (or all policies when shown in a clustered visual).
**Used in:** Page 6 — policy comparison bar/line chart (policy_name on axis/legend).

### 17. Baseline Safety Stock
```dax
Baseline Safety Stock =
SUM ( materials[safety_stock] )
```
**Plain English:** total safety stock currently set in the material master — the "as-is" reference every policy is compared against.
**Used in:** Page 6 — baseline reference line; input to Cost Delta vs Baseline.

### 18. Policy Holding Cost (INR/yr)
```dax
Policy Holding Cost (INR/yr) =
SUMX (
    VALUES ( materials[material_id] ),
    CALCULATE ( SUM ( inventory_policy_params[safety_stock] ) )
        * CALCULATE ( MAX ( materials[unit_cost] ) )
        * 0.22
)
```
**Plain English:** annual holding cost of the policy's safety stock (SS × unit cost × 22%).
**Used in:** Page 6 — cost comparison visual.

### 19. Baseline Holding Cost (INR/yr)
```dax
Baseline Holding Cost (INR/yr) =
SUMX (
    VALUES ( materials[material_id] ),
    CALCULATE ( MAX ( materials[safety_stock] ) )
        * CALCULATE ( MAX ( materials[unit_cost] ) )
        * 0.22
)
```
**Plain English:** annual holding cost of the current (baseline) safety-stock settings.
**Used in:** Page 6 — baseline cost reference.

### 20. Cost Delta vs Baseline
```dax
Cost Delta vs Baseline =
[Policy Holding Cost (INR/yr)] - [Baseline Holding Cost (INR/yr)]
```
**Plain English:** extra (positive) or saved (negative) annual holding cost of the selected policy versus current settings — the "what does this policy cost us?" number.
**Used in:** Page 6 — KPI card + waterfall-style comparison.

### 21. Reorder Point (Policy)
```dax
Reorder Point (Policy) =
SUM ( inventory_policy_params[reorder_point] )
```
**Plain English:** total reorder-point quantity under the selected policy.
**Used in:** Page 6 — policy parameter table; Page 4 reorder-review list.

---

## E. Time-intelligence measures

### 22. Rolling 4-Week Receipts
```dax
Rolling 4-Week Receipts =
CALCULATE (
    SUM ( goods_receipts[received_qty] ),
    DATESINPERIOD ( dim_date[date], MAX ( dim_date[date] ), -28, DAY ),
    USERELATIONSHIP ( dim_date[date], goods_receipts[receipt_date] )
)
```
**Plain English:** quantity received in the 28 days ending at the current date — smooths lumpy receipts for the supply trend.
**Used in:** Page 4 — receipts vs issues trend chart.

### 23. MTD Material Issues
```dax
MTD Material Issues =
CALCULATE (
    - SUM ( inventory_transactions[signed_qty] ),
    inventory_transactions[type] = "ISSUE",
    DATESMTD ( dim_date[date] )
)
```
**Plain English:** total material quantity consumed month-to-date (issues stored negative, hence the minus sign). Uses the active `transaction_date` relationship.
**Used in:** Page 2 — consumption KPI card; Page 4 trend.

### 24. MoM Issues Change %
```dax
MoM Issues Change % =
VAR CurMonth =
    CALCULATE (
        - SUM ( inventory_transactions[signed_qty] ),
        inventory_transactions[type] = "ISSUE",
        DATESMTD ( dim_date[date] )
    )
VAR PrevMonth =
    CALCULATE (
        - SUM ( inventory_transactions[signed_qty] ),
        inventory_transactions[type] = "ISSUE",
        PREVIOUSMONTH ( dim_date[date] )
    )
RETURN
    DIVIDE ( CurMonth - PrevMonth, PrevMonth, 0 )
```
**Plain English:** month-on-month percentage change in material consumption — flags demand surges feeding the shortage engine.
**Used in:** Page 1 — demand-trend indicator; Page 2 conditional formatting.

> **Validation tip:** after pasting, check `[Usable Stock]` for one material (e.g. `MAT-0001`) against SQL: `SELECT SUM(signed_qty) FROM inventory_transactions WHERE material_id='MAT-0001';` — the numbers must match when no date filter is applied. See the reconciliation checklist in `dashboard_implementation.md`.
