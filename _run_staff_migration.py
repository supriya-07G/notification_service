"""Run the staff roles migration on the existing database."""
import sqlite3

conn = sqlite3.connect("storage.sqlite")

# Run each ALTER TABLE separately — SQLite will error if column already exists
alterations = [
    "ALTER TABLE admin_users ADD COLUMN role TEXT CHECK(role IN ('admin','staff')) DEFAULT 'staff'",
    "ALTER TABLE admin_users ADD COLUMN name TEXT DEFAULT ''",
    "ALTER TABLE admin_users ADD COLUMN phone TEXT",
    "ALTER TABLE admin_users ADD COLUMN force_password_reset BOOLEAN DEFAULT 0",
    "ALTER TABLE admin_users ADD COLUMN updated_at TEXT DEFAULT '2024-01-01 00:00:00'",
]

for sql in alterations:
    try:
        conn.execute(sql)
        col_name = sql.split("ADD COLUMN ")[1].split(" ")[0]
        print(f"  Added column: {col_name}")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            col_name = sql.split("ADD COLUMN ")[1].split(" ")[0]
            print(f"  Column already exists: {col_name}")
        else:
            raise

# Promote all existing users to admin (including those already set to 'staff' by default)
conn.execute("UPDATE admin_users SET role = 'admin'")
conn.commit()
print("\nAll existing users promoted to 'admin' role.")

# Verify
rows = conn.execute("SELECT id, email, role, is_active FROM admin_users").fetchall()
print(f"\nCurrent admin_users ({len(rows)} total):")
for r in rows:
    print(f"  id={r[0]} email={r[1]} role={r[2]} active={r[3]}")

conn.close()
print("\nMigration complete!")
