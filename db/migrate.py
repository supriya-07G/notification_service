"""Database migration script.

Reads db/schema.sql and executes it using get_connection().
Safe to run multiple times — all CREATE TABLE uses IF NOT EXISTS.
"""
import os
import sys

# Ensure project root is on sys.path so `db.init` and `config` resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.init import get_connection


def run_migration():
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection()
    try:
        conn.executescript(schema_sql)
        print("Migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
