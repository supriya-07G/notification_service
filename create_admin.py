#!/usr/bin/env python3
"""create_admin.py — Interactive CLI to create an admin user.

Usage:
    python create_admin.py

Creates a user in the admin_users table with role='admin'
and a proper per-user bcrypt password hash.
"""

import getpass
import sys

from dotenv import load_dotenv

load_dotenv()

from db.init import get_connection
from db.admin_users import (
    is_allowed_email,
    validate_password_strength,
    hash_password,
)


def main():
    print("=" * 50)
    print("  EcoSave — Create Admin User")
    print("=" * 50)
    print()

    # 1. Collect email
    email = input("Email: ").strip().lower()
    if not email:
        print("Error: Email is required.")
        sys.exit(1)

    if not is_allowed_email(email):
        print(f"Error: Email must end with @ecosave-group.com")
        sys.exit(1)

    # 2. Collect name
    name = input("Full Name: ").strip()
    if not name:
        print("Error: Name is required.")
        sys.exit(1)

    # 3. Collect phone (optional)
    phone = input("Phone (optional): ").strip() or None

    # 4. Collect and validate password
    password = getpass.getpass("Password: ")
    errors = validate_password_strength(password)
    if errors:
        print("\nPassword does not meet requirements:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    password_confirm = getpass.getpass("Confirm Password: ")
    if password != password_confirm:
        print("Error: Passwords do not match.")
        sys.exit(1)

    # 5. Hash and insert
    hashed = hash_password(password)

    conn = get_connection()
    try:
        # Check if user already exists
        existing = conn.execute(
            "SELECT id FROM admin_users WHERE email = ?", [email]
        ).fetchone()

        if existing:
            print(f"\nUser {email} already exists (id={existing['id']}).")
            update = input("Update to admin with new password? (y/N): ").strip().lower()
            if update != "y":
                print("Aborted.")
                sys.exit(0)

            conn.execute(
                """UPDATE admin_users
                   SET password_hash = ?, role = 'admin', name = ?, phone = ?,
                       is_active = 1, updated_at = CURRENT_TIMESTAMP
                   WHERE email = ?""",
                [hashed, name, phone, email],
            )
            conn.commit()
            print(f"\nUpdated {email} to admin with new password.")
        else:
            conn.execute(
                """INSERT INTO admin_users (email, password_hash, role, name, phone, is_active)
                   VALUES (?, ?, 'admin', ?, ?, 1)""",
                [email, hashed, name, phone],
            )
            conn.commit()
            print(f"\nCreated admin user: {email}")

        print("Done! You can now log in at /dashboard/login")

    except Exception as e:
        print(f"\nDatabase error: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
