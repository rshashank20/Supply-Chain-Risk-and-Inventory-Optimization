-- ============================================================================
-- schema.sql — Shortage DSS database schema
-- Project : Risk-Aware Safety-Stock Optimization and Material Shortage DSS
--           (final-year Industrial Engineering major project)
-- DATA    : ALL data in this project is SYNTHETIC, produced by the seeded
--           generator at ../src/data_generation/generate_data.py (seed 42,
--           config in ../config/project_config.yaml). Nothing here represents
--           a real company, real suppliers, or real transactions.
-- USE     : sqlite3 data/processed/shortage_dss.db < database/schema.sql
--           DDL is idempotent (IF NOT EXISTS). FK enforcement is left to the
--           application (PRAGMA foreign_keys = ON when needed).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- MASTER DATA
-- ----------------------------------------------------------------------------

-- Finished goods made by the plant. active_flag = 1 means currently produced.
CREATE TABLE IF NOT EXISTS products (
    product_id  TEXT PRIMARY KEY,
    product_family TEXT,
    unit        TEXT,
    active_flag INTEGER
);

-- Raw materials / components. criticality A/B/C drives shortage attention;
-- safety_stock here is the seeded baseline (see inventory_policy_params for
-- the experimented policies).
CREATE TABLE IF NOT EXISTS materials (
    material_id  TEXT PRIMARY KEY,
    category     TEXT,
    unit         TEXT,
    unit_cost    REAL,
    criticality  TEXT,
    safety_stock REAL
);

-- Single-level bill of materials: how much of each material one unit of a
-- product consumes. qty_per_product is inflated by scrap_allowance at
-- explosion time. effective_to IS NULL = currently effective.
CREATE TABLE IF NOT EXISTS bom_lines (
    bom_line_id     INTEGER PRIMARY KEY,
    product_id      TEXT REFERENCES products(product_id),
    material_id     TEXT REFERENCES materials(material_id),
    qty_per_product REAL,
    scrap_allowance REAL,
    effective_from  TEXT,
    effective_to    TEXT
);

-- Approved vendors. capacity_band A/B/C is a coarse size proxy.
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id   TEXT PRIMARY KEY,
    region        TEXT,
    status        TEXT,
    capacity_band TEXT
);

-- Approved supplier-material pairs with commercial terms. is_primary = 1
-- marks the preferred source; materials with exactly one row are
-- single-sourced (a key shortage risk).
CREATE TABLE IF NOT EXISTS supplier_material (
    supplier_id  TEXT REFERENCES suppliers(supplier_id),
    material_id  TEXT REFERENCES materials(material_id),
    lead_time_days INTEGER,
    unit_price   REAL,
    moq          REAL,
    max_capacity REAL,
    is_primary   INTEGER,
    PRIMARY KEY (supplier_id, material_id)
);

-- ----------------------------------------------------------------------------
-- PLANNING
-- ----------------------------------------------------------------------------

-- Shop-floor work orders. status: completed / in_progress / planned.
-- Gross material requirements are exploded from these via bom_lines.
CREATE TABLE IF NOT EXISTS production_orders (
    production_order_id TEXT PRIMARY KEY,
    product_id   TEXT REFERENCES products(product_id),
    planned_qty  REAL,
    release_date TEXT,
    required_date TEXT,
    priority     TEXT,
    status       TEXT
);

-- ----------------------------------------------------------------------------
-- PROCUREMENT
-- ----------------------------------------------------------------------------

-- Purchase order headers (one per supplier per ordering event).
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id         TEXT PRIMARY KEY,
    supplier_id   TEXT REFERENCES suppliers(supplier_id),
    order_date    TEXT,
    status        TEXT,
    currency_code TEXT
);

-- Purchase order lines: the actual buy quantities and promised dates.
-- A line may be fulfilled by several goods_receipts (split deliveries).
CREATE TABLE IF NOT EXISTS purchase_order_lines (
    po_line_id   TEXT PRIMARY KEY,
    po_id        TEXT REFERENCES purchase_orders(po_id),
    material_id  TEXT REFERENCES materials(material_id),
    order_qty    REAL,
    promised_date TEXT,
    unit_price   REAL
);

-- Inbound deliveries. rejected_qty is QC-rejected stock (excluded from usable
-- inventory); accepted qty = received_qty - rejected_qty.
CREATE TABLE IF NOT EXISTS goods_receipts (
    receipt_id   INTEGER PRIMARY KEY,
    po_line_id   TEXT REFERENCES purchase_order_lines(po_line_id),
    receipt_date TEXT,
    received_qty REAL,
    rejected_qty REAL
);

-- ----------------------------------------------------------------------------
-- INVENTORY
-- ----------------------------------------------------------------------------

-- The inventory ledger: every stock movement as a signed quantity.
-- type: OPENING (day-1 balances), RECEIPT, ISSUE, ADJUSTMENT.
-- Running balance per material/date is built by v_inventory_ledger.
CREATE TABLE IF NOT EXISTS inventory_transactions (
    transaction_id   INTEGER PRIMARY KEY,
    material_id      TEXT REFERENCES materials(material_id),
    transaction_date TEXT,
    type             TEXT,
    signed_qty       REAL,
    reference_id     TEXT
);

-- ----------------------------------------------------------------------------
-- RISK
-- ----------------------------------------------------------------------------

-- Identified supply risks (single_source, supplier_delivery, quality,
-- material_availability). likelihood/impact are 1-5 scores; priority =
-- likelihood * impact. status: open / mitigating / (closed).
CREATE TABLE IF NOT EXISTS risk_register (
    risk_id     INTEGER PRIMARY KEY,
    category    TEXT,
    material_id TEXT,
    supplier_id TEXT,
    likelihood  INTEGER,
    impact      INTEGER,
    owner_role  TEXT,
    status      TEXT
);

-- Realised risk events (late_delivery, quality_hold, ...). A 6-week
-- disruption of one supplier (Mar-Apr 2025) is baked into the generator.
CREATE TABLE IF NOT EXISTS risk_events (
    event_id        INTEGER PRIMARY KEY,
    risk_id         INTEGER REFERENCES risk_register(risk_id),
    event_date      TEXT,
    event_type      TEXT,
    severity        INTEGER,
    estimated_loss  REAL,
    disruption_days INTEGER
);

-- Mitigation actions linked to risks, with cost, due date and outcome.
CREATE TABLE IF NOT EXISTS mitigation_actions (
    action_id      INTEGER PRIMARY KEY,
    risk_id        INTEGER REFERENCES risk_register(risk_id),
    action_type    TEXT,
    estimated_cost REAL,
    due_date       TEXT,
    status         TEXT,
    outcome        TEXT
);

-- ----------------------------------------------------------------------------
-- CALENDAR / POLICY / SCENARIOS
-- ----------------------------------------------------------------------------

-- Date dimension: one row per day of the 24-month history window
-- (2024-10-01 .. 2026-09-30). week is the ISO week number.
CREATE TABLE IF NOT EXISTS dim_date (
    date       TEXT PRIMARY KEY,
    year       INTEGER,
    month      INTEGER,
    quarter    INTEGER,
    week       INTEGER,
    is_weekend INTEGER
);

-- Baseline inventory policies per material, the seed for the safety-stock
-- optimizer experiments: weeks_of_cover_4, normal_ss_95, minmax.
-- source = 'baseline' for generator output; optimizer writes new rows.
CREATE TABLE IF NOT EXISTS inventory_policy_params (
    policy_id     INTEGER PRIMARY KEY,
    material_id   TEXT REFERENCES materials(material_id),
    policy_name   TEXT,
    service_level REAL,
    safety_stock  REAL,
    reorder_point REAL,
    order_up_to   REAL,
    review_weeks  REAL,
    source        TEXT
);

-- What-if scenario definitions for the disruption simulator.
CREATE TABLE IF NOT EXISTS scenario_config (
    scenario_id  TEXT PRIMARY KEY,
    name         TEXT,
    demand_mult  REAL,
    lt_mult      REAL,
    defect_mult  REAL,
    description  TEXT
);

-- Monte-Carlo experiment output per (scenario, policy, replication).
-- Empty until the simulation experiments are run.
CREATE TABLE IF NOT EXISTS simulation_results (
    result_id           INTEGER PRIMARY KEY,
    scenario_id         TEXT REFERENCES scenario_config(scenario_id),
    policy_name         TEXT,
    replication         INTEGER,
    fill_rate           REAL,
    cycle_service_level REAL,
    stockout_count      INTEGER,
    avg_inventory_value REAL,
    total_cost          REAL
);

-- ----------------------------------------------------------------------------
-- HELPFUL INDEXES (the generator ships none beyond PK auto-indexes)
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_txn_mat_date
    ON inventory_transactions (material_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_txn_type
    ON inventory_transactions (type);
CREATE INDEX IF NOT EXISTS idx_receipts_poline
    ON goods_receipts (po_line_id);
CREATE INDEX IF NOT EXISTS idx_receipts_date
    ON goods_receipts (receipt_date);
CREATE INDEX IF NOT EXISTS idx_poline_po
    ON purchase_order_lines (po_id);
CREATE INDEX IF NOT EXISTS idx_poline_mat
    ON purchase_order_lines (material_id);
CREATE INDEX IF NOT EXISTS idx_po_supplier
    ON purchase_orders (supplier_id);
CREATE INDEX IF NOT EXISTS idx_bom_product
    ON bom_lines (product_id);
CREATE INDEX IF NOT EXISTS idx_bom_material
    ON bom_lines (material_id);
CREATE INDEX IF NOT EXISTS idx_prodord_reqdate
    ON production_orders (required_date, status);
CREATE INDEX IF NOT EXISTS idx_sm_material
    ON supplier_material (material_id);
CREATE INDEX IF NOT EXISTS idx_policy_mat
    ON inventory_policy_params (material_id, policy_name);
CREATE INDEX IF NOT EXISTS idx_riskevents_risk
    ON risk_events (risk_id);
CREATE INDEX IF NOT EXISTS idx_mitigation_risk
    ON mitigation_actions (risk_id);
CREATE INDEX IF NOT EXISTS idx_dimdate_yw
    ON dim_date (year, week);
