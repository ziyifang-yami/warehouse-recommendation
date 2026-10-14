"""
LOC Recommendation — Data Loader
==================================
Loads SKU and LOC data from MySQL (or a CSV upload).
All SQL uses parameterized queries via SQLAlchemy text().
"""

import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# ---------------------------------------------------------------------------
# DB Connection
# ---------------------------------------------------------------------------

def _get_engine():
    user = os.environ["MYSQL_USER"]
    password = os.environ["MYSQL_PASS"]
    host = os.environ.get("MYSQL_HOST", "rds.g3.yamibuy.net")
    url = f"mysql+pymysql://{user}:{password}@{host}/yamibuy_wh?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True)


# ---------------------------------------------------------------------------
# Main: Load SKU velocity + LOC candidates
# ---------------------------------------------------------------------------

def load_sku_velocity(
    start_date: date | None = None,
    end_date: date | None = None,
    warehouse: str = "All",
) -> pd.DataFrame:
    """
    Returns one row per SKU with 30-day pick velocity and current LOC.

    Columns: sku, sku_name, current_loc, zone, velocity_30d,
             weight_kg, volume_cbm, temperature_zone
    """
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    wh_filter = ""
    if warehouse != "All":
        wh_filter = f"AND wi.warehouse_code = '{warehouse}'"

    sql = f"""
    SELECT
        ii.item_number                            AS sku,
        ii.item_name                              AS sku_name,
        wl.loc_code                               AS current_loc,
        wl.zone_code                              AS zone,
        COUNT(od.order_detail_id)                 AS velocity_30d,
        COALESCE(ii.weight_kg, 0)                 AS weight_kg,
        COALESCE(ii.volume_cbm, 0)                AS volume_cbm,
        COALESCE(ii.temperature_zone, 'AMBIENT')  AS temperature_zone
    FROM yamibuy_wh.wh_inventory wi
    JOIN yamibuy_wh.wh_loc wl         ON wl.loc_id    = wi.loc_id
    JOIN yamibuy_master.item_info ii  ON ii.item_number = wi.item_number
    LEFT JOIN yamibuy_wh.wh_outbound_detail od
           ON od.item_number = wi.item_number
          AND od.pick_dtm   >= UNIX_TIMESTAMP('{start_date.isoformat()}')
          AND od.pick_dtm    < UNIX_TIMESTAMP('{end_date.isoformat()}')
          {wh_filter}
    WHERE wi.qty_on_hand > 0
      AND ii.item_name IS NOT NULL
      {wh_filter.replace('wi.warehouse_code', 'wi.warehouse_code')}
    GROUP BY
        ii.item_number, ii.item_name,
        wl.loc_code, wl.zone_code,
        ii.weight_kg, ii.volume_cbm, ii.temperature_zone
    ORDER BY velocity_30d DESC
    """

    engine = _get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)

    if not df.empty:
        df["velocity_30d"] = df["velocity_30d"].astype(int)
        df["weight_kg"] = pd.to_numeric(df["weight_kg"], errors="coerce").fillna(0)
        df["volume_cbm"] = pd.to_numeric(df["volume_cbm"], errors="coerce").fillna(0)

    return df


def load_loc_master(warehouse: str = "All") -> pd.DataFrame:
    """
    Returns all available LOCs with capacity metadata.

    Columns: loc, zone, max_weight_kg, max_volume_cbm,
             distance_m, temperature_zone, reserve_distance_m
    """
    wh_filter = ""
    if warehouse != "All":
        wh_filter = f"WHERE wl.warehouse_code = '{warehouse}'"

    sql = f"""
    SELECT
        wl.loc_code                               AS loc,
        wl.zone_code                              AS zone,
        COALESCE(wl.max_weight_kg, 999)           AS max_weight_kg,
        COALESCE(wl.max_volume_cbm, 999)          AS max_volume_cbm,
        COALESCE(wl.distance_from_dock_m, 50)     AS distance_m,
        COALESCE(wl.temperature_zone, 'AMBIENT')  AS temperature_zone,
        COALESCE(wl.reserve_distance_m, 100)      AS reserve_distance_m
    FROM yamibuy_wh.wh_loc wl
    {wh_filter}
    ORDER BY wl.zone_code, wl.loc_code
    """

    engine = _get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)

    return df


# ---------------------------------------------------------------------------
# CSV fallback loader
# ---------------------------------------------------------------------------

def load_from_csv(uploaded_file) -> pd.DataFrame:
    """Parse an uploaded CSV (st.file_uploader object)."""
    df = pd.read_csv(uploaded_file)
    df.columns = [c.strip().lower() for c in df.columns]
    return df
