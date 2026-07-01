"""auth/csrf.py — CSRF token generation and validation.

Uses itsdangerous.URLSafeTimedSerializer to sign CSRF tokens with expiry.
Tokens are included as hidden fields in login forms and validated on POST.
"""

import hmac
import os

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")

_csrf_serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY)

CSRF_MAX_AGE = 3600


def generate_csrf_token(session_id: str | None = None) -> str:
    """Generate a signed CSRF token bound to an optional session id."""
    payload = {"csrf": True}
    if session_id is not None:
        payload["session_id"] = session_id
    return _csrf_serializer.dumps(payload)


def validate_csrf_token(token: str, session_id: str | None = None) -> bool:
    """Validate a CSRF token and optionally require it to match a session id."""
    if not token:
        return False
    try:
        data = _csrf_serializer.loads(token, max_age=CSRF_MAX_AGE)
        if data.get("csrf") is not True:
            return False
        if session_id is None:
            return True
        return data.get("session_id") == session_id
    except (BadSignature, SignatureExpired):
        return False
