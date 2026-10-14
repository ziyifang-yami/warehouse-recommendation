import os
from dotenv import load_dotenv
load_dotenv('/opt/loc-recommendation/.env')
from sqlalchemy import create_engine, text
import pandas as pd

engine = create_engine(
    f"mysql+pymysql://{os.environ['MYSQL_USER']}:{os.environ['MYSQL_PASS']}@{os.environ.get('MYSQL_HOST','rds.g3.yamibuy.net')}/yamibuy_wh?charset=utf8mb4",
    pool_pre_ping=True,
)

with engine.connect() as c:
    # 1. segment value distribution for WH 002
    print("=== segment distribution (warehouse 002) ===")
    df = pd.read_sql(text("""
        SELECT segment, COUNT(*) as cnt
        FROM yamibuy_bigdata.po_product_segment
        WHERE warehouse_number='002' AND is_deleted=0
        GROUP BY segment ORDER BY segment
    """), c)
    print(df.to_string())

    # 2. test the recent SKU
    for sku in ['5030037751', '1021006541', '1021023071']:
        df2 = pd.read_sql(text("""
            SELECT item_number, segment, max_14_day_sale_qty, update_time
            FROM yamibuy_bigdata.po_product_segment
            WHERE item_number=:sku AND warehouse_number='002' AND is_deleted=0
            ORDER BY update_time DESC LIMIT 1
        """), c, params={"sku": sku})
        print(f"\nSKU {sku}: {df2.to_string() if not df2.empty else 'NOT FOUND'}")
