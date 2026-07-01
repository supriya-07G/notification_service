"""auth/csrf.py — CSRF token generation and validation.

Uses itsdangerous.URLSafeTimedSerializer to sign CSRF tokens with expiry.
Tokens are included as hidden fields in login forms and validated on POST.
"""

import os

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")

_csrf_serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY)

CSRF_MAX_AGE = 3600


def generate_csrf_token(session_id: str | None = None) -> str:
    """Generate a signed CSRF token for form inclusion."""
    payload = {"csrf": True}
    if session_id is not None:
        payload["session_id"] = session_id
    return _csrf_serializer.dumps(payload)


def validate_csrf_token(token: str, session_id: str | None = None) -> bool:
    """Validate a CSRF token. Returns True if valid and not expired, False otherwise.

    The optional session_id parameter is reserved for the later S9 session-binding
    work; for now we keep validation backward-compatible and only verify the
    signature and expiry.
    """
    if not token:
        return False
    try:
        data = _csrf_serializer.loads(token, max_age=CSRF_MAX_AGE)
        return data.get("csrf") is True
    except (BadSignature, SignatureExpired):
        return False
