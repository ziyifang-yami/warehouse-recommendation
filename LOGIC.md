# LOC Recommendation — Putaway Logic

## Overview

This tool helps warehouse associates decide whether a newly received inbound batch should be put away to **NJFC (Zone 1)** or **SFC (Zone 2)**, based on expiry date comparison with existing inventory.

Scope: **Warehouse 002 only.**

---

## User Flow

```
Step 1 — Scan Batch Number (BT barcode)
            ↓
Step 2 — Scan UPC or type SKU
            ↓
    System looks up batch record + product info
            ↓
    System queries Zone 1 current inventory expiry
            ↓
    Displays putaway recommendation: TO NJFC or TO SFC
```

---

## Zone Definitions

| Zone | Facility | zone_id | Description |
|------|----------|---------|-------------|
| Zone 1 | **NJFC** | 1 | Primary pick zone — should always hold the freshest stock |
| Zone 2 | **SFC** | 2+ | Secondary storage — holds older or overflow stock |

The goal is FIFO by expiry: **NJFC should never hold stock that expires later than what's in SFC.** Fresher product goes into Zone 1 to ensure it gets picked first.

---

## Decision Logic

### Inputs

| Input | Source | Field |
|-------|--------|-------|
| Incoming batch expiry | `wh_inbound_batch.qc_ExpiredDate` | Unix timestamp → `DATE` |
| Zone 1 worst expiry | `wh_storage_location_item.expire_dtm` (SKU-level, preferred) or `wh_storage_location.expire_dtm` (location-level fallback) | Unix timestamp → `DATE` |

### Decision Tree

```
Has incoming batch an expiry date?
├── NO  → ⚠️ Cannot determine zone (show warning, no recommendation)
└── YES → Query Zone 1 current inventory for worst (earliest) expiry date
          │
          ├── Zone 1 has NO stock (or no expiry data)
          │     → Route to SFC (Zone 2)
          │       Reason: no existing stock to compare against
          │
          ├── Zone 1 worst_exp < incoming_exp
          │   (Zone 1 stock expires EARLIER than this batch)
          │     → Route to NJFC (Zone 1)  ✅
          │       Reason: Zone 1 holds older stock; this fresher batch should
          │               fill Zone 1 so it gets picked before the older stock
          │
          └── Zone 1 worst_exp >= incoming_exp
              (Zone 1 stock expires SAME or LATER than this batch)
                → Route to SFC (Zone 2)
                  Reason: Zone 1 already has fresher or equivalent stock;
                          this batch is older/equal and should go to SFC
```

### Summary Table

| Zone 1 state | Decision | Banner |
|---|---|---|
| No stock in Zone 1 | **TO SFC** | 🟢 Green |
| Zone 1 expires **before** this batch | **TO NJFC** | 🔴 Red |
| Zone 1 expires **same day or after** this batch | **TO SFC** | 🟢 Green |
| No expiry on incoming batch | ⚠️ No recommendation | — |

---

## Zone 1 Expiry Query

The system reads Zone 1 expiry from two tables with a priority order:

1. **`wh_storage_location_item`** (SKU-level) — checked first. Stores expiry per SKU per location. This is the most accurate source.
2. **`wh_storage_location`** (location-level) — fallback when no SKU-level record exists.

Filter conditions:
- `warehouse_number = '002'`
- `zone_id = 1`
- `location_type IN (3, 4)` — STOCK and BIN Pick locations only (excludes staging, receiving, etc.)
- `quantity > 0` — only locations with live inventory
- `expire_dtm > 0` — only locations with a known expiry date

The query returns `MIN(expire_dtm)` — the **earliest** (worst) expiry across all qualifying Zone 1 locations for that SKU.

```sql
SELECT MIN(
    IF(sli.rec_id IS NULL, sl.expire_dtm, sli.expire_dtm)
) AS zone1_worst_exp_dtm
FROM wh_inventory_transaction inv
JOIN wh_storage_location sl
    ON inv.location_no = sl.location_no AND inv.warehouse_number = sl.warehouse_number
LEFT JOIN wh_storage_location_item sli
    ON sl.location_no = sli.location_no AND sl.warehouse_number = sli.warehouse_number
    AND sli.item_number = inv.item_number
WHERE inv.warehouse_number = '002'
  AND inv.item_number = :sku
  AND inv.quantity > 0
  AND sl.zone_id = 1
  AND sl.location_type IN (3, 4)
  AND IF(sli.rec_id IS NULL, sl.expire_dtm, sli.expire_dtm) > 0
```

---

## Batch Expiry Source

Incoming batch expiry is read from `wh_inbound_batch.qc_ExpiredDate` (Unix timestamp), matched by `rec_Id = CAST(SUBSTRING(:bt_number, 3) AS UNSIGNED)` (stripping the `BT` prefix) and `item_number`.

---

## Visual Output

| Result | Background | Border | Label |
|--------|-----------|--------|-------|
| TO SFC | `#d4edda` (light green) | `#28a745` | **TO SFC** |
| TO NJFC | `#f8d7da` (light red) | `#dc3545` | **TO NJFC** |

Both banners display a sub-line explaining the reason (Zone 1 expiry vs incoming batch expiry).

---

## Error / Edge Cases

| Condition | Behaviour |
|-----------|-----------|
| `qc_ExpiredDate = 0` or NULL on incoming batch | Warning: "No expiry date on this batch — cannot determine putaway zone." |
| No Zone 1 inventory (or all locations have `expire_dtm = 0`) | Route to SFC |
| DB connection unavailable | Warning displayed; no recommendation shown |
| SKU not found in item master | Error: "UPC not in item master" |
| BT number not matching any batch for that SKU | Error: "No record for this BT + SKU" |

---

## Limitations & Future Improvements

- Currently supports **WH 002 only**. Logic can be extended to other warehouses by parameterising `zone_id` mapping per warehouse.
- Only considers **expiry date** for the putaway decision. Future versions could incorporate velocity (pick frequency), space availability per zone, or temperature zone requirements.
- The `location_type IN (3, 4)` filter covers STOCK and BIN Pick. If zone layout changes, this filter may need updating.
