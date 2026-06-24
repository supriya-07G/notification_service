"""auth/csrf.py — CSRF token generation and validation.

Uses itsdangerous.URLSafeSerializer to sign CSRF tokens.
Tokens are included as hidden fields in login forms and validated on POST.
"""

import os

from itsdangerous import URLSafeSerializer, BadSignature

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")

_csrf_serializer = URLSafeSerializer(SESSION_SECRET_KEY)


def generate_csrf_token() -> str:
    """Generate a signed CSRF token for form inclusion."""
    return _csrf_serializer.dumps({"csrf": True})


def validate_csrf_token(token: str) -> bool:
    """Validate a CSRF token. Returns True if valid, False otherwise."""
    if not token:
        return False
    try:
        data = _csrf_serializer.loads(token)
        return data.get("csrf") is True
    except BadSignature:
        return False
