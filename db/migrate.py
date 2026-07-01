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


def _migrate_dedup_key(conn) -> None:
    """Fix the notification_attempts dedup UNIQUE key (L1 fix).

    The old constraint was UNIQUE(appointment_id, rule_name, channel, appointment_at).
    Including appointment_at meant that rescheduling an appointment created a new key,
    causing all three reminder rules to fire again for the rescheduled appointment.

    The new constraint is UNIQUE(appointment_id, rule_name, channel) — dedup is per
    appointment × rule × channel, regardless of the appointment time.  The appointment_at
    column is kept as an audit snapshot but no longer participates in the unique key.

    This migration is idempotent: it checks for the old constraint before running.
    """
    # Check if the old index (with appointment_at) still exists
    old_idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='notification_attempts_appointment_id_rule_name_channel_appoin'"
    ).fetchone()

    # Also check by inspecting the unique index columns
    indexes = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='notification_attempts'"
    ).fetchall()

    has_old_constraint = any(
        idx["sql"] and "appointment_at" in idx["sql"] and "UNIQUE" in (idx["sql"] or "")
        for idx in indexes
    )

    if not has_old_constraint:
        return  # Already migrated or fresh install from updated schema.sql

    print("  Migrating notification_attempts dedup key (removing appointment_at from UNIQUE constraint)...")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notification_attempts_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id TEXT NOT NULL REFERENCES appointments(id),
            appointment_at TIMESTAMP NOT NULL,
            rule_name TEXT NOT NULL,
            channel TEXT NOT NULL,
            to_address TEXT NOT NULL,
            provider_sid TEXT,
            status TEXT DEFAULT 'pending',
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status_updated_at TIMESTAMP,
            error_code TEXT,
            error_message TEXT,
            UNIQUE (appointment_id, rule_name, channel)
        );

        INSERT OR IGNORE INTO notification_attempts_new
            (id, appointment_id, appointment_at, rule_name, channel, to_address,
             provider_sid, status, sent_at, status_updated_at, error_code, error_message)
        SELECT id, appointment_id, appointment_at, rule_name, channel, to_address,
               provider_sid, status, sent_at, status_updated_at, error_code, error_message
        FROM notification_attempts;

        DROP TABLE notification_attempts;
        ALTER TABLE notification_attempts_new RENAME TO notification_attempts;

        CREATE INDEX IF NOT EXISTS idx_attempts_appt
            ON notification_attempts(appointment_id, rule_name, channel);
        CREATE INDEX IF NOT EXISTS idx_attempts_sid
            ON notification_attempts(provider_sid);
    """)
    print("  Dedup key migration complete.")


def run_migration():
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection()
    try:
        conn.executescript(schema_sql)

        # Additive column migrations (ALTER TABLE for existing DBs)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMP
            )
        """)
        _add_column_if_missing(conn, "sessions", "revoked_at", "TIMESTAMP")
        _add_column_if_missing(conn, "inbound_messages", "resolved",    "BOOLEAN DEFAULT FALSE")
        _add_column_if_missing(conn, "inbound_messages", "resolved_at", "TIMESTAMP")
        _add_column_if_missing(conn, "inbound_messages", "resolved_by", "TEXT")
        # oauth_states is created by schema.sql (CREATE TABLE IF NOT EXISTS)

        # Migrate role column: add super_admin support, rename 'staff' -> 'user'
        _migrate_roles(conn)
        _migrate_dedup_key(conn)

        conn.commit()

        print("Migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
