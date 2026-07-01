"""auth/session.py — Session management with signed cookies and rate limiting.

Uses itsdangerous.URLSafeTimedSerializer for session token signing.
Session stored in HttpOnly, SameSite=Lax cookie named 'ns_session'.

Rule 2: Never log session tokens or secrets.
"""

import logging
import os
import sqlite3
import time
from typing import Optional

from db.init import get_connection

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")
SESSION_SECURE_COOKIE = os.getenv("SESSION_SECURE_COOKIE", "true").lower() == "true"
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_SECONDS", "28800"))  # 8 hours

COOKIE_NAME = "ns_session"
_failed_attempts: dict[str, list[float]] = {}

# Validate secret key at import time
if len(SESSION_SECRET_KEY) < 32:
    raise RuntimeError(
        "SESSION_SECRET_KEY must be at least 32 characters. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
# Reject the .env.example placeholder — it is public and would make all session
# and CSRF tokens forgeable.
if SESSION_SECRET_KEY == "change_me_to_a_secure_random_string":
    raise RuntimeError(
        "SESSION_SECRET_KEY is still the .env.example placeholder. "
        "Set a real secret: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

_serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY)

# ── Rate limiting (persistent, per-IP) ──────────────────────────────────────
# Backed by the login_attempts table so limits survive restarts and are shared
# across all uvicorn workers (the old in-memory dict was per-process and reset
# on every deploy).

RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 900  # 15 minutes


def _get_client_ip(request: Request) -> str:
    """Extract the client IP from the request."""
    direct_ip = request.client.host if request.client else "unknown"
    trusted_proxies = os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1").split(",")
    trusted_proxies = [ip.strip() for ip in trusted_proxies if ip.strip()]

    # If direct IP is not a trusted proxy, ignore X-Forwarded-For
    if direct_ip not in trusted_proxies:
        return direct_ip

    # If it is a trusted proxy, check X-Forwarded-For.
    # Use the RIGHTMOST entry — the leftmost is client-supplied and can be
    # spoofed to bypass rate limiting.  The rightmost is what the last
    # trusted proxy (e.g. nginx/Cloudflare) actually appended.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
        
    return direct_ip


def _ensure_session_tables(conn) -> None:
    """Create auth-related tables on demand for fresh or test databases."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMP
        )
        """
    )
    conn.commit()


def _prune_rate_limit_entries(now: float) -> None:
    """Drop expired entries from the in-memory rate-limit bucket."""
    expired_keys = [
        ip for ip, attempts in _failed_attempts.items()
        if not any(now - ts <= RATE_LIMIT_WINDOW_SECONDS for ts in attempts)
    ]
    for ip in expired_keys:
        _failed_attempts.pop(ip, None)


def is_rate_limited(request: Request) -> bool:
    """Return True if the IP has exceeded the rate limit within the window."""
    ip = _get_client_ip(request)
    now = time.time()
    _prune_rate_limit_entries(now)
    if ip in _failed_attempts and len(_failed_attempts[ip]) >= RATE_LIMIT_MAX_ATTEMPTS:
        return True

    conn = get_connection()
    try:
        _ensure_session_tables(conn)
        row = conn.execute(
            f"""SELECT COUNT(*) FROM login_attempts
                WHERE ip = ? AND attempted_at >= datetime('now', '-{RATE_LIMIT_WINDOW_SECONDS} seconds')""",
            (ip,),
        ).fetchone()
        return (row[0] if row else 0) >= RATE_LIMIT_MAX_ATTEMPTS
    finally:
        conn.close()


def record_failed_attempt(request: Request) -> None:
    """Record a failed login attempt for the client IP (persistent)."""
    ip = _get_client_ip(request)
    now = time.time()
    _prune_rate_limit_entries(now)
    _failed_attempts.setdefault(ip, []).append(now)

    conn = get_connection()
    try:
        _ensure_session_tables(conn)
        conn.execute("INSERT INTO login_attempts (ip) VALUES (?)", (ip,))
        # Opportunistic cleanup of rows older than the window.
        conn.execute(
            f"DELETE FROM login_attempts WHERE attempted_at < datetime('now', '-{RATE_LIMIT_WINDOW_SECONDS} seconds')"
        )
        conn.commit()
    finally:
        conn.close()


def clear_failed_attempts(request: Request) -> None:
    """Clear failed attempts for the client IP (on successful login)."""
    ip = _get_client_ip(request)
    _failed_attempts.pop(ip, None)

    conn = get_connection()
    try:
        _ensure_session_tables(conn)
        conn.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
        conn.commit()
    finally:
        conn.close()


# ── Session cookie operations ───────────────────────────────────────────────

def _get_user_from_db(conn, email: str | None = None, user_id: int | None = None) -> Optional[dict]:
    """Load the current user from the database and reject inactive accounts."""
    if not email and user_id is None:
        return None

    if user_id is not None:
        row = conn.execute(
            "SELECT id, email, role, is_active, force_password_reset FROM admin_users WHERE id = ?",
            [user_id],
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, email, role, is_active, force_password_reset FROM admin_users WHERE email = ? COLLATE NOCASE",
            [email.strip().lower()],
        ).fetchone()

    if not row:
        return None
    if row["is_active"] != 1:
        return None

    return {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"] or "user",
        "force_password_reset": bool(row["force_password_reset"]),
    }


def get_current_user(request: Request) -> Optional[dict]:
    """Extract and validate the session cookie against the database.

    Returns a dict with email, role, force_password_reset, session_id, and user_id
    when the session is valid, or None otherwise.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    email = (data.get("email") or "").strip().lower()
    if not email:
        return None

    session_id = data.get("session_id")
    user_id = data.get("user_id")

    conn = get_connection()
    try:
        user_row = _get_user_from_db(conn, email=email, user_id=user_id)
        if not user_row:
            return None

        if session_id:
            session_row = conn.execute(
                "SELECT id, revoked_at FROM sessions WHERE id = ?",
                [session_id],
            ).fetchone()
            if not session_row or session_row["revoked_at"] is not None:
                return None

            conn.execute(
                "UPDATE sessions SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                [session_id],
            )
            conn.commit()

        force_reset = 1 if user_row["force_password_reset"] else 0
        return {
            "email": user_row["email"],
            "role": user_row["role"],
            "force_password_reset": force_reset,
            "session_id": session_id,
            "user_id": user_row["id"],
        }
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def create_session_cookie(
    response: Response,
    user_email: str,
    role: str = "user",
    force_password_reset: bool = False,
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    """Sign the user identity and session metadata into a session token and set it as a cookie."""
    token = _serializer.dumps({
        "email": user_email,
        "role": role,
        "force_password_reset": bool(force_password_reset),
        "session_id": session_id,
        "user_id": user_id,
    })
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=SESSION_SECURE_COOKIE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Delete the session cookie."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=SESSION_SECURE_COOKIE,
    )


def revoke_session(request: Request) -> None:
    """Mark the current session (or all sessions for the user) as revoked."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return

    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return

    email = (data.get("email") or "").strip().lower()
    session_id = data.get("session_id")

    conn = get_connection()
    try:
        if session_id:
            conn.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP, last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                [session_id],
            )
        elif email:
            conn.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP, last_used_at = CURRENT_TIMESTAMP WHERE email = ?",
                [email],
            )
        conn.commit()
    finally:
        conn.close()


def require_role(request: Request, allowed_roles: list[str]) -> Optional[RedirectResponse]:
    """Check if the user has one of the allowed roles.

    Returns a RedirectResponse if not authenticated or not authorized, or None if OK.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    if user["role"] not in allowed_roles:
        return RedirectResponse(url="/dashboard/", status_code=302)
    return None


def require_login(request: Request) -> Optional[RedirectResponse]:
    """Check if the user is authenticated.

    Returns a RedirectResponse to the login page if not authenticated,
    or None if the user is logged in.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    return None
