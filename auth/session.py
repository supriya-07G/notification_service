"""auth/session.py — Session management with signed cookies and rate limiting.

Uses itsdangerous.URLSafeTimedSerializer for session token signing.
Session stored in HttpOnly, SameSite=Lax cookie named 'ns_session'.

Rule 2: Never log session tokens or secrets.
"""

import logging
import os
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")
SESSION_SECURE_COOKIE = os.getenv("SESSION_SECURE_COOKIE", "true").lower() == "true"
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_SECONDS", "28800"))  # 8 hours

COOKIE_NAME = "ns_session"

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

# ── Rate limiting (in-memory, per-IP) ───────────────────────────────────────

# Structure: { ip_address: [timestamp1, timestamp2, ...] }
_failed_attempts: dict[str, list[float]] = {}

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


def is_rate_limited(request: Request) -> bool:
    """Return True if the IP has exceeded the rate limit."""
    ip = _get_client_ip(request)
    now = time.time()
    attempts = _failed_attempts.get(ip, [])

    # Filter to only attempts within the window
    recent = [t for t in attempts if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _failed_attempts[ip] = recent

    return len(recent) >= RATE_LIMIT_MAX_ATTEMPTS


def record_failed_attempt(request: Request) -> None:
    """Record a failed login attempt for the client IP."""
    ip = _get_client_ip(request)
    now = time.time()

    if ip not in _failed_attempts:
        _failed_attempts[ip] = []

    _failed_attempts[ip].append(now)


def clear_failed_attempts(request: Request) -> None:
    """Clear failed attempt counter for the client IP (on successful login)."""
    ip = _get_client_ip(request)
    _failed_attempts.pop(ip, None)


# ── Session cookie operations ───────────────────────────────────────────────

def get_current_user(request: Request) -> Optional[dict]:
    """Extract and validate the session cookie.

    Returns a dict {'email': str, 'role': str} if the session is valid, or None.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        email = data.get("email")
        if not email:
            return None
        return {"email": email, "role": data.get("role", "user")}
    except (BadSignature, SignatureExpired):
        return None


def create_session_cookie(response: Response, user_email: str, role: str = "user") -> None:
    """Sign the user email and role into a session token and set it as a cookie."""
    token = _serializer.dumps({"email": user_email, "role": role})
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
