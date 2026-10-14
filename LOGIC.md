# Putaway Guide — Decision Logic

**App:** `http://zbot-agent.yamibuy.net:8504`  
**Scope:** Warehouse 002 (NJ FC + SFC)  
**Updated:** 2026-10-14

---

## Overview

When a receiving associate scans a BT or GT barcode, the system:
1. Resolves the SKU automatically from the batch record
2. Looks up the SKU's **sales segment** and **live inventory state**
3. Applies the putaway decision tree
4. Displays a color-coded recommendation: **FC**, **SFC**, or **Keep in Current Staging Area**

---

## Zone Definitions

| Zone | Facility | Description |
|------|----------|-------------|
| **FC** | NJ Fulfillment Center | Primary pick zone — high-velocity, always replenished |
| **SFC** | Secondary FC | Overflow/backup storage — feeds FC via T1/T2 transfers |
| **Staging Area** | Receiving | New product staging — hold until classified |

---

## Step 1 — Scan Input

Scan either:
- **BT barcode** (`BT{rec_Id}`) — matches `wh_inbound_batch.rec_Id`
- **GT barcode** (`GT…`) — matches `wh_inbound_batch.group_batch_id`

Both automatically resolve to: SKU, quantity, expiry date, received time, receiver.

---

## Step 2 — Segment Classification

The SKU's **sales segment** is determined from the NJ Allocation cache (`sfc_cache.db`):

| Segment | Criteria |
|---------|----------|
| **TBD** | `inv_days_30 < 30` OR `inv_days_30 = NaN` (new product, in stock < 30 days) |
| **TOP** | `active_order_days ≥ 21` AND `order_lines_30d ≥ 30` |
| **TAIL** | `order_lines_30d < 8` |
| **MIDDLE** | All others |

`need_minimal_exp` (expiry tracking flag) is read in real-time from `yamibuy_im.im_item_extend`.

---

## Step 3 — Putaway Decision Tree

### TBD — New Product

```
→ Keep in Current Staging Area  (TBD_KEEP)
Reason: Product has been in stock < 30 days — hold at receiving zone
        until classified by the next Allocation cycle.
```

---

### TOP — High Velocity

```
→ FC  (TOP_FC)
Reason: TOP SKUs always live in FC for fast replenishment.
```

---

### TAIL — Low Velocity

```
→ SFC  (TAIL_SFC)
Reason: TAIL SKUs always live in SFC — transferred to FC only when needed.
```

---

### MIDDLE — Moderate Velocity

Decision is made in this order:

```
Step 1 — FEFO check vs FC:
  need_minimal_exp = 1
  AND new_exp < fc_min_exp_date (FC's oldest lot expiry)
  → FC  (MID_EXP_EARLIER_FC)
  Reason: New goods expire earlier than FC stock — put in FC first (FEFO)

Step 2 — FC has enough stock:
  FC D2S > 30 days
  → SFC  (MID_FC_D2S_HIGH)
  Reason: FC already has sufficient stock (>30 days) — put new goods in SFC

Step 3 — FC needs replenishment, SFC has stock:
  FC D2S ≤ 30 AND sfc_qty > 0
  ├─ Sub-check: need_minimal_exp = 1 AND new_exp ≤ sfc_min_exp_date
  │    → FC  (MID_SFC_EXP_EARLIER_FC)
  │    Reason: New goods expire earlier/same as SFC's oldest lot
  │            — put in FC first (FEFO vs SFC)
  └─ Otherwise:
       → SFC  (MID_SFC_HAS_STOCK)
       Reason: SFC has stock — put new goods in SFC, wait for T2 to replenish FC

Step 4 — FC needs replenishment, SFC is empty:
  FC D2S ≤ 30 AND sfc_qty = 0
  → FC  (MID_SFC_EMPTY)
  Reason: Both FC and SFC are low — put directly in FC
```

---

## Decision Summary Table

| Segment | Condition | → Zone | Rule |
|---------|-----------|--------|------|
| TBD | Any | Keep in Staging | TBD_KEEP |
| TOP | Any | FC | TOP_FC |
| TAIL | Any | SFC | TAIL_SFC |
| MIDDLE | `need_exp=1` AND `new_exp < fc_min_exp` | FC | MID_EXP_EARLIER_FC |
| MIDDLE | `fc_d2s > 30` | SFC | MID_FC_D2S_HIGH |
| MIDDLE | `fc_d2s ≤ 30`, `sfc>0`, `need_exp=1`, `new_exp ≤ sfc_min_exp` | FC | MID_SFC_EXP_EARLIER_FC |
| MIDDLE | `fc_d2s ≤ 30`, `sfc>0` | SFC | MID_SFC_HAS_STOCK |
| MIDDLE | `fc_d2s ≤ 30`, `sfc=0` | FC | MID_SFC_EMPTY |
| Any | No segment data | SFC | NO_SEG (fallback) |

---

## Data Sources

| Field | Source | Notes |
|-------|--------|-------|
| SKU, qty, expiry, receiver | `wh_inbound_batch` | Via BT (rec_Id) or GT (group_batch_id) |
| Product name | `im_item` → `xysc_goods` | EN name for EN/ES; ZH name for ZH |
| Segment (TOP/MIDDLE/TAIL/TBD) | `sfc_cache.db → sales + tbd_skus` | Refreshed every 12h by Allocation cron |
| `avg_daily_orders` | `sfc_cache.db → sales` | Used to compute FC D2S |
| `need_minimal_exp` | `yamibuy_im.im_item_extend` (real-time) | 1 = expiry-tracked product |
| FC qty, SFC qty | `wh_inventory_transaction` (real-time) | zone_id != 2 → FC; zone_id = 2 → SFC |
| FC min expiry | `wh_storage_location_item` / `wh_storage_location` (real-time) | MIN exp across FC lots |
| SFC min expiry | `wh_storage_location_item` / `wh_storage_location` (real-time) | MIN exp across SFC lots |
| FC D2S | `fc_qty / avg_daily_orders` | Computed on the fly |

---

## Thresholds (configurable)

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `putaway_d2s` | 30 days | MIDDLE: FC D2S above this → SFC |
| `_TBD_DAYS` | 30 days | TBD: inv_days_30 below this → TBD |
| `_SEG_AOD_TOP` | 21 days | TOP: active order days threshold |
| `_SEG_OL_TOP` | 30 lines | TOP: order lines (30d) threshold |
| `_SEG_OL_TAIL` | 8 lines | TAIL: order lines (30d) below this |

---

## Color Coding

| Color | Zone | Meaning |
|-------|------|---------|
| 🔴 Red | FC | Put in Fulfillment Center |
| 🟢 Green | SFC | Put in Secondary FC |
| 🟡 Yellow | Staging | Keep in Current Staging Area (TBD) |

---

## Languages

The UI supports **EN / ES / ZH** (select via top-right dropdown, persisted in localStorage).  
In ZH mode, product names display in Chinese (`goods_name`).  
All putaway reasons are translated server-side based on the `lang` parameter.
