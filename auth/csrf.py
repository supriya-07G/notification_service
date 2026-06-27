"""auth/csrf.py — CSRF token generation and validation.

Uses itsdangerous.URLSafeTimedSerializer to sign CSRF tokens with expiry.
Tokens are included as hidden fields in login forms and validated on POST.
"""

import os

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")

_csrf_serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY)

CSRF_MAX_AGE = 3600


def generate_csrf_token() -> str:
    """Generate a signed CSRF token for form inclusion."""
    return _csrf_serializer.dumps({"csrf": True})


def validate_csrf_token(token: str) -> bool:
    """Validate a CSRF token. Returns True if valid and not expired, False otherwise."""
    if not token:
        return False
    try:
        data = _csrf_serializer.loads(token, max_age=CSRF_MAX_AGE)
        return data.get("csrf") is True
    except (BadSignature, SignatureExpired):
        return False
