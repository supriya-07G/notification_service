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


def _migrate_roles(conn):
    """Migrate from two-tier (admin/staff) to three-tier (super_admin/admin/user) roles."""
    # Check if migration is needed by looking at the CHECK constraint
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='admin_users'"
    ).fetchone()
    if not table_sql or 'super_admin' in table_sql[0]:
        return  # Already migrated or table doesn't exist

    # SQLite requires table recreation to change CHECK constraints
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS admin_users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            phone TEXT,
            role TEXT CHECK(role IN ('super_admin','admin','user')) DEFAULT 'user',
            is_active BOOLEAN DEFAULT TRUE,
            force_password_reset BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO admin_users_new (id, email, password_hash, name, phone, role, is_active, force_password_reset, created_at, last_login_at, updated_at)
            SELECT id, email, password_hash, name, phone,
                   CASE WHEN role = 'staff' THEN 'user' ELSE role END,
                   is_active, force_password_reset, created_at, last_login_at, updated_at
            FROM admin_users;

        DROP TABLE admin_users;
        ALTER TABLE admin_users_new RENAME TO admin_users;

        CREATE INDEX IF NOT EXISTS idx_admin_users_email ON admin_users(email);
    """)

    # Set super admins
    conn.execute(
        "UPDATE admin_users SET role = 'super_admin' WHERE email IN (?, ?)",
        ['michael@ecosave-group.com', 'polly@ecosave-group.com']
    )


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
        # oauth_states is created by schema.sql (CREATE TABLE IF NOT EXISTS)

        # Migrate role column: add super_admin support, rename 'staff' -> 'user'
        _migrate_roles(conn)

        conn.commit()

        print("Migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
