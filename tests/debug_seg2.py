"""Check po_product_segment table structure and sample data"""
import os, traceback
from dotenv import load_dotenv
load_dotenv('/opt/loc-recommendation/.env')
from sqlalchemy import create_engine, text
import pandas as pd

engine = create_engine(
    f"mysql+pymysql://{os.environ['MYSQL_USER']}:{os.environ['MYSQL_PASS']}@{os.environ.get('MYSQL_HOST','rds.g3.yamibuy.net')}/yamibuy_wh?charset=utf8mb4",
    pool_pre_ping=True,
)

with engine.connect() as c:
    # 1. schema
    print("=== yamibuy_bigdata.po_product_segment columns ===")
    df = pd.read_sql(text("SHOW COLUMNS FROM yamibuy_bigdata.po_product_segment"), c)
    print(df[['Field','Type']].to_string())

    # 2. sample
    print("\n=== Sample rows ===")
    df2 = pd.read_sql(text("SELECT * FROM yamibuy_bigdata.po_product_segment LIMIT 5"), c)
    print(df2.to_string())

    # 3. count + freshness
    print("\n=== Count + latest update ===")
    df3 = pd.read_sql(text("""
        SELECT COUNT(*) as cnt,
               MAX(updated_at) as latest,
               COUNT(DISTINCT segment) as distinct_segs
        FROM yamibuy_bigdata.po_product_segment
    """), c)
    print(df3.to_string())

    # 4. distinct segment values
    print("\n=== Distinct segment values ===")
    df4 = pd.read_sql(text("""
        SELECT segment, COUNT(*) as cnt
        FROM yamibuy_bigdata.po_product_segment
        GROUP BY segment ORDER BY cnt DESC
    """), c)
    print(df4.to_string())

    # 5. test lookup with a real SKU
    test_sku = "5030037751"
    print(f"\n=== Lookup SKU {test_sku} ===")
    df5 = pd.read_sql(text("""
        SELECT * FROM yamibuy_bigdata.po_product_segment
        WHERE item_number = :sku
    """), c, params={"sku": test_sku})
    print(df5.to_string() if not df5.empty else "  Not found")
