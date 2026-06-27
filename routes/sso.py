"""routes/sso.py — Google OAuth 2.0 SSO login.

Adds Google Sign-In as an additional login method alongside email/password.
Existing auth (routes/dashboard.py login form) is NOT touched.

Flow:
  GET /auth/google/login     → redirect to Google with a random state param
  GET /auth/google/callback  → validate state, exchange code, get user info,
                               enforce @ecosave-group.com domain,
                               get-or-create admin_users row,
                               set the same ns_session cookie as password login,
                               redirect to /dashboard/

Security:
  - state stored in oauth_states table (5-minute TTL)
  - domain restricted to @ecosave-group.com
  - rate-limited (same in-memory store as password login)
  - session cookie: HttpOnly, Secure, SameSite=Lax (via create_session_cookie)
  - no secrets logged
"""

import logging
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import config
from auth.session import (
    create_session_cookie,
    is_rate_limited,
    record_failed_attempt,
    clear_failed_attempts,
)
from db.admin_users import get_or_create_staff_from_sso
from db.init import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["sso"])

ALLOWED_DOMAIN = "@ecosave-group.com"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = "openid email profile"


def _redirect_uri() -> str:
    return config.WEBHOOK_BASE_URL.rstrip("/") + "/auth/google/callback"


def _store_state(conn, state: str) -> None:
    conn.execute(
        """INSERT INTO oauth_states (state)
           VALUES (?)
           ON CONFLICT(state) DO NOTHING""",
        [state],
    )
    conn.commit()


def _validate_and_consume_state(conn, state: str) -> bool:
    """Return True if state exists and is not expired; delete it either way."""
    row = conn.execute(
        """SELECT id FROM oauth_states
           WHERE state = ?
             AND expires_at > datetime('now')""",
        [state],
    ).fetchone()
    # Always delete (consumed or expired)
    conn.execute("DELETE FROM oauth_states WHERE state = ?", [state])
    conn.commit()
    return row is not None


def _error_redirect(msg: str) -> RedirectResponse:
    params = urllib.parse.urlencode({"error": msg})
    return RedirectResponse(url=f"/dashboard/login?{params}", status_code=302)


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/login")
async def sso_login(request: Request):
    """Redirect the browser to Google's OAuth consent screen."""
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        return _error_redirect("Google SSO is not configured.")

    if is_rate_limited(request):
        return _error_redirect("Too many attempts. Try again in 15 minutes.")

    state = secrets.token_urlsafe(32)
    conn = get_connection()
    try:
        _store_state(conn, state)
    finally:
        conn.close()

    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the OAuth callback from Google."""
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        return _error_redirect("Google SSO is not configured.")

    # 1. Rate limit check
    if is_rate_limited(request):
        return _error_redirect("Too many attempts. Try again in 15 minutes.")

    # 2. Google returned an error (user denied, etc.)
    if error:
        logger.info("Google SSO declined by user: %s", error)
        return _error_redirect("Authentication failed.")

    if not code or not state:
        record_failed_attempt(request)
        return _error_redirect("Authentication failed.")

    # 3. Validate state
    conn = get_connection()
    try:
        if not _validate_and_consume_state(conn, state):
            record_failed_attempt(request)
            logger.warning("SSO callback: invalid or expired state from ip=%s",
                           request.client.host if request.client else "?")
            return _error_redirect("Authentication failed.")
    finally:
        conn.close()

    # 4. Exchange code for tokens
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            })
            token_resp.raise_for_status()
            token_data = token_resp.json()

            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()

    except Exception:
        logger.exception("SSO: failed to exchange token or fetch user info")
        record_failed_attempt(request)
        return _error_redirect("Authentication failed.")

    email: str = (userinfo.get("email") or "").strip().lower()
    name: str = userinfo.get("name") or email.split("@")[0]

    # 5. Domain restriction
    if not email.endswith(ALLOWED_DOMAIN):
        record_failed_attempt(request)
        logger.warning("SSO: rejected non-company email %s", email)
        return _error_redirect("Only @ecosave-group.com accounts are allowed.")

    # 6. Get or create admin_users row
    try:
        conn = get_connection()
        try:
            user = get_or_create_staff_from_sso(conn, email, name)
        finally:
            conn.close()
    except Exception:
        logger.exception("SSO: DB error for email=%s", email)
        record_failed_attempt(request)
        return _error_redirect("Authentication failed.")

    # 7. Create session (same cookie as password login)
    clear_failed_attempts(request)
    response = RedirectResponse(url="/dashboard/", status_code=302)
    create_session_cookie(response, user["email"], user["role"])
    logger.info("SSO login: email=%s role=%s", user["email"], user["role"])
    return response


@router.get("/logout")
async def sso_logout(request: Request):
    """Clear session and redirect to login page."""
    from auth.session import clear_session_cookie
    response = RedirectResponse(url="/dashboard/login", status_code=302)
    clear_session_cookie(response)
    return response
