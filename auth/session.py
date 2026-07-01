"""auth/session.py — Session management with signed cookies and rate limiting.

Uses itsdangerous.URLSafeTimedSerializer for session token signing.
Session stored in HttpOnly, SameSite=Lax cookie named 'ns_session'.

Rule 2: Never log session tokens or secrets.
"""

import logging
import os
import time
from typing import Optional

from db.init import get_connection

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from db.init import get_connection

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

    # If it is a trusted proxy, check X-Forwarded-For
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
        
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
            last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def get_current_user(request: Request) -> Optional[dict]:
    """Extract and validate the session cookie.

    Returns a dict with email, role, force_password_reset, session_id, and user_id
    when the session is valid, or None otherwise.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        email = data.get("email")
        if not email:
            return None

        force_reset = data.get("force_password_reset")
        if force_reset in (1, True, "1", "true"):
            force_reset_value = 1
        else:
            force_reset_value = 0

        return {
            "email": email,
            "role": data.get("role", "user"),
            "force_password_reset": force_reset_value,
            "session_id": data.get("session_id"),
            "user_id": data.get("user_id"),
        }
    except (BadSignature, SignatureExpired):
        return None


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
