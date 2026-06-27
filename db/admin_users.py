"""db/admin_users.py — Admin user management with bcrypt hashing.

Handles email domain restriction, password strength validation,
secure password hashing via passlib/bcrypt, and authentication.

Rule 2: Never store or log plaintext passwords.
"""

import logging
import re
import secrets

# pyrefly: ignore [missing-import]
from passlib.context import CryptContext

from db.init import get_connection

logger = logging.getLogger(__name__)

# bcrypt with default 12 rounds
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALLOWED_DOMAIN = "@ecosave-group.com"

SPECIAL_CHARS = set("!@#$%^&*()_-=+[]{}|;:,.<>?")


def is_allowed_email(email: str) -> bool:
    """Return True if the email ends with the allowed domain (case-insensitive)."""
    if not email or not isinstance(email, str):
        return False
    return email.strip().lower().endswith(ALLOWED_DOMAIN)


def validate_password_strength(password: str) -> list[str]:
    """Return a list of unmet password requirements. Empty list = valid."""
    errors = []
    if len(password) < 12:
        errors.append("Password must be at least 12 characters long.")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter (A-Z).")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter (a-z).")
    if not re.search(r"[0-9]", password):
        errors.append("Password must contain at least one digit (0-9).")
    if not any(c in SPECIAL_CHARS for c in password):
        errors.append(
            "Password must contain at least one special character: !@#$%^&*()_-=+[]{}|;:,.<>?"
        )
    return errors


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given password."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain, hashed)


def authenticate(email: str, password: str) -> dict | None:
    """Authenticate a user by email and password.

    Checks per-user password hash first (from admin_users table).
    Falls back to the globally shared password if the user has no individual hash.
    Returns a dict with user info on success, or None on failure.
    Always runs bcrypt verify even if missing hash (timing attack prevention).
    """
    if not is_allowed_email(email):
        pwd_context.dummy_verify()
        return None

    email_clean = email.strip().lower()
    conn = get_connection()
    try:
        # Check if user exists with a per-user password
        user_row = conn.execute(
            "SELECT id, password_hash, role FROM admin_users WHERE email = ? AND is_active = 1",
            [email_clean],
        ).fetchone()

        if user_row and user_row["password_hash"] and user_row["password_hash"] != "SHARED":
            # Per-user password authentication
            if not verify_password(password, user_row["password_hash"]):
                return None
            # Update last_login
            conn.execute(
                "UPDATE admin_users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                [user_row["id"]],
            )
            conn.commit()
            return {
                "id": user_row["id"],
                "email": email_clean,
                "role": user_row["role"] or "staff",
            }

        # Fallback: shared global password
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'dashboard_password_hash'"
        ).fetchone()
        
        if not row or not row["value"]:
            # Hardcoded hashes removed for security (P0 fix)
            raise RuntimeError(
                "A global dashboard password hash must be set in system_settings or "
                "an admin user created via create_admin.py"
            )
        else:
            hashed = row["value"]

        if not verify_password(password, hashed):
            return None

        # Success with shared password — upsert user record
        conn.execute(
            """
            INSERT INTO admin_users (email, password_hash, last_login_at)
            VALUES (?, 'SHARED', CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE SET
                last_login_at = CURRENT_TIMESTAMP
            """,
            [email_clean]
        )
        conn.commit()

        # Fetch ID and role
        user_row = conn.execute(
            "SELECT id, role FROM admin_users WHERE email = ?", [email_clean]
        ).fetchone()
        user_id = user_row["id"] if user_row else 0
        user_role = user_row["role"] if user_row and user_row["role"] else "staff"

        return {"id": user_id, "email": email_clean, "role": user_role}
    finally:
        conn.close()


def get_or_create_staff_from_sso(conn, email: str, name: str) -> dict:
    """Return existing admin_users row or create a new one for SSO users.

    SSO users get:
      - role = 'staff'
      - is_active = 1
      - force_password_reset = 0
      - hashed_password = random bcrypt (they will always use SSO)
      - phone = NULL
    """
    email_clean = email.strip().lower()

    row = conn.execute(
        "SELECT id, email, role, is_active FROM admin_users WHERE email = ?",
        [email_clean],
    ).fetchone()

    if row:
        if not row["is_active"]:
            raise ValueError(f"Account {email_clean} is inactive.")
        conn.execute(
            "UPDATE admin_users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
            [row["id"]],
        )
        conn.commit()
        return {"id": row["id"], "email": email_clean, "role": row["role"] or "staff"}

    # New SSO user — generate a random password hash they will never use
    random_password = secrets.token_hex(32)
    password_hash = pwd_context.hash(random_password)

    conn.execute(
        """INSERT INTO admin_users
               (email, name, password_hash, role, is_active, force_password_reset, last_login_at)
           VALUES (?, ?, ?, 'staff', 1, 0, CURRENT_TIMESTAMP)""",
        [email_clean, name, password_hash],
    )
    conn.commit()
    new_id = conn.execute(
        "SELECT id FROM admin_users WHERE email = ?", [email_clean]
    ).fetchone()["id"]
    logger.info("SSO: created new staff account for %s", email_clean)
    return {"id": new_id, "email": email_clean, "role": "staff"}
