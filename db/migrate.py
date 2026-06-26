"""Database migration script.

Reads db/schema.sql and executes it using get_connection().
Safe to run multiple times — all CREATE TABLE uses IF NOT EXISTS.
"""
import os
import sys

# Ensure project root is on sys.path so `db.init` and `config` resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.init import get_connection


def _add_column_if_missing(conn, table: str, column: str, definition: str):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        print(f"  Added column {table}.{column}")


def run_migration():
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection()
    try:
        conn.executescript(schema_sql)

        # Additive column migrations (ALTER TABLE for existing DBs)
        _add_column_if_missing(conn, "inbound_messages", "resolved",    "BOOLEAN DEFAULT FALSE")
        _add_column_if_missing(conn, "inbound_messages", "resolved_at", "TIMESTAMP")
        _add_column_if_missing(conn, "inbound_messages", "resolved_by", "TEXT")
        conn.commit()

        print("Migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
