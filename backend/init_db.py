"""
init_db.py
──────────
Reads init_schema.sql and executes it against the configured PostgreSQL DB.
Run once before starting any pipeline service.

Usage:
    python init_db.py
"""

import os
import sys
import psycopg2
from common.config import DB_DSN

SQL_FILE = os.path.join(os.path.dirname(__file__), "init_schema.sql")


def init_schema() -> None:
    sql = open(SQL_FILE, encoding="utf-8").read()

    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print("✅  Schema initialised successfully.")
    except Exception as exc:
        conn.rollback()
        print(f"❌  Schema initialisation failed: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_schema()

    # ad_inventory is always dropped and recreated by init_schema.sql.
    # Automatically re-populate it so the system is ready to run immediately.
    print("⏳  Populating ad inventory …")
    from populate_ad_inventory import populate
    populate()
