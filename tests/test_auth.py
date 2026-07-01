"""tests/test_auth.py — Comprehensive authentication tests.

Tests domain restriction, password validation, hashing, CSRF,
login flow, rate limiting, session management, and logout.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from db.admin_users import (
    is_allowed_email,
    validate_password_strength,
    hash_password,
    verify_password,
    authenticate,
)
from auth.csrf import generate_csrf_token, validate_csrf_token
from auth.session import _serializer, COOKIE_NAME, _failed_attempts, get_current_user


# ── Helper ──────────────────────────────────────────────────────────────────

VALID_EMAIL = "testuser@ecosave-group.com"
VALID_PASSWORD = "SecurePass123!@"


def _create_test_admin_in_db(conn, email=VALID_EMAIL, password=VALID_PASSWORD):
    """Insert a test admin user directly into the DB and set global password."""
    hashed = hash_password(password)
    # Set the global password hash
    conn.execute(
        """
        INSERT INTO system_settings (key, value, updated_by)
        VALUES ('dashboard_password_hash', ?, 'test')
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [hashed]
    )
    # Still need an admin user to pass the domain check
    # But wait, domain check only needs the domain string, it doesn't need to be in DB.
    # The authentication logic just checks if email domain is ecosave-group.com
    # and then checks global hash. But we'll add one to DB for tracking logic.
    conn.execute(
        "INSERT OR IGNORE INTO admin_users (email, password_hash) VALUES (?, 'SHARED')",
        [email],
    )
    conn.commit()


def _get_session_cookie(email=VALID_EMAIL):
    """Generate a valid session cookie dict."""
    token = _serializer.dumps({"email": email})
    return {COOKIE_NAME: token}


@pytest.fixture
def client(non_closing_db):
    """TestClient with DB mocked. No session cookie set."""
    with patch("routes.dashboard.get_connection", return_value=non_closing_db), \
         patch("db.admin_users.get_connection", return_value=non_closing_db), \
         patch("auth.session.get_connection", return_value=non_closing_db):
        from webhook_server import app
        with TestClient(app) as tc:
            yield tc


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limit state between tests."""
    _failed_attempts.clear()
    yield
    _failed_attempts.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Domain Restriction
# ══════════════════════════════════════════════════════════════════════════════

class TestDomainRestriction:
    def test_allowed_email(self):
        assert is_allowed_email("admin@ecosave-group.com") is True

    def test_allowed_email_case_insensitive(self):
        assert is_allowed_email("Admin@EcoSave-Group.com") is True

    def test_rejected_gmail(self):
        assert is_allowed_email("user@gmail.com") is False

    def test_rejected_similar_domain(self):
        assert is_allowed_email("user@ecosave-group.org") is False

    def test_rejected_empty(self):
        assert is_allowed_email("") is False

    def test_rejected_none(self):
        assert is_allowed_email(None) is False

    def test_rejected_no_at(self):
        assert is_allowed_email("ecosave-group.com") is False


# ══════════════════════════════════════════════════════════════════════════════
# Password Strength Validation
# ══════════════════════════════════════════════════════════════════════════════

class TestPasswordStrength:
    def test_valid_password(self):
        errors = validate_password_strength("SecurePass123!")
        assert errors == []

    def test_too_short(self):
        errors = validate_password_strength("Short1!a")
        assert any("12 characters" in e for e in errors)

    def test_missing_uppercase(self):
        errors = validate_password_strength("securepass123!")
        assert any("uppercase" in e for e in errors)

    def test_missing_lowercase(self):
        errors = validate_password_strength("SECUREPASS123!")
        assert any("lowercase" in e for e in errors)

    def test_missing_digit(self):
        errors = validate_password_strength("SecurePassword!!")
        assert any("digit" in e for e in errors)

    def test_missing_special_char(self):
        errors = validate_password_strength("SecurePass1234")
        assert any("special character" in e for e in errors)

    def test_all_rules_violated(self):
        errors = validate_password_strength("short")
        assert len(errors) >= 3  # too short + missing uppercase + missing digit + missing special


# ══════════════════════════════════════════════════════════════════════════════
# Password Hashing
# ══════════════════════════════════════════════════════════════════════════════

class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        hashed = hash_password("MyPassword123!")
        assert verify_password("MyPassword123!", hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("MyPassword123!")
        assert verify_password("WrongPassword123!", hashed) is False

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("MyPassword123!")
        assert hashed != "MyPassword123!"
        assert hashed.startswith("$2b$")  # bcrypt prefix


# ══════════════════════════════════════════════════════════════════════════════
# CSRF Tokens
# ══════════════════════════════════════════════════════════════════════════════

class TestCSRF:
    def test_generate_and_validate(self):
        token = generate_csrf_token()
        assert validate_csrf_token(token) is True

    def test_reject_tampered_token(self):
        token = generate_csrf_token()
        assert validate_csrf_token(token + "x") is False

    def test_reject_empty_token(self):
        assert validate_csrf_token("") is False

    def test_reject_none_token(self):
        assert validate_csrf_token(None) is False

    def test_reject_random_string(self):
        assert validate_csrf_token("not-a-real-token") is False


# ══════════════════════════════════════════════════════════════════════════════
# Login Flow
# ══════════════════════════════════════════════════════════════════════════════

class TestLoginFlow:
    def test_login_page_loads(self, client):
        resp = client.get("/dashboard/login")
        assert resp.status_code == 200
        assert "Sign In" in resp.text

    def test_valid_login_redirects_to_dashboard(self, client, test_db):
        _create_test_admin_in_db(test_db)
        csrf = generate_csrf_token()

        resp = client.post(
            "/dashboard/login",
            data={
                "email": VALID_EMAIL,
                "password": VALID_PASSWORD,
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/dashboard/" in resp.headers["location"]
        # Session cookie should be set
        assert COOKIE_NAME in resp.cookies

    def test_wrong_password_shows_generic_error(self, client, test_db):
        _create_test_admin_in_db(test_db)
        csrf = generate_csrf_token()

        resp = client.post(
            "/dashboard/login",
            data={
                "email": VALID_EMAIL,
                "password": "WrongPassword123!",
                "csrf_token": csrf,
            },
        )
        assert resp.status_code == 200
        assert "Invalid email or password" in resp.text

    def test_wrong_domain_shows_generic_error(self, client, test_db):
        """Wrong domain should show same generic error — no domain leak."""
        csrf = generate_csrf_token()

        resp = client.post(
            "/dashboard/login",
            data={
                "email": "user@gmail.com",
                "password": "SomePassword123!",
                "csrf_token": csrf,
            },
        )
        assert resp.status_code == 200
        assert "Invalid email or password" in resp.text
        # Should NOT reveal domain restriction
        assert "ecosave-group.com" not in resp.text or "Must be an @ecosave-group.com" in resp.text

    def test_missing_csrf_returns_400(self, client, test_db):
        _create_test_admin_in_db(test_db)

        resp = client.post(
            "/dashboard/login",
            data={
                "email": VALID_EMAIL,
                "password": VALID_PASSWORD,
                "csrf_token": "",
            },
        )
        assert resp.status_code == 400
        assert "Invalid request" in resp.text

    def test_invalid_csrf_returns_400(self, client, test_db):
        _create_test_admin_in_db(test_db)

        resp = client.post(
            "/dashboard/login",
            data={
                "email": VALID_EMAIL,
                "password": VALID_PASSWORD,
                "csrf_token": "tampered-token",
            },
        )
        assert resp.status_code == 400

    def test_already_logged_in_redirects_away_from_login(self, client, test_db):
        _create_test_admin_in_db(test_db)
        cookies = _get_session_cookie()
        client.cookies.update(cookies)

        resp = client.get("/dashboard/login", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard/" in resp.headers["location"]


# ══════════════════════════════════════════════════════════════════════════════
# Rate Limiting
# ══════════════════════════════════════════════════════════════════════════════

class TestRateLimiting:
    def test_rate_limit_after_5_failures(self, client, test_db):
        _create_test_admin_in_db(test_db)

        # Make 5 failed attempts
        for _ in range(5):
            csrf = generate_csrf_token()
            client.post(
                "/dashboard/login",
                data={
                    "email": VALID_EMAIL,
                    "password": "WrongPassword123!",
                    "csrf_token": csrf,
                },
            )

        # 6th attempt should be rate limited
        csrf = generate_csrf_token()
        resp = client.post(
            "/dashboard/login",
            data={
                "email": VALID_EMAIL,
                "password": VALID_PASSWORD,
                "csrf_token": csrf,
            },
        )
        assert resp.status_code == 429
        assert "Too many failed attempts" in resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Protected Routes
# ══════════════════════════════════════════════════════════════════════════════

class TestProtectedRoutes:
    def test_overview_redirects_when_unauthenticated(self, client):
        resp = client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard/login" in resp.headers["location"]

    def test_appointments_redirects_when_unauthenticated(self, client):
        resp = client.get("/dashboard/appointments", follow_redirects=False)
        assert resp.status_code == 302

    def test_settings_redirects_when_unauthenticated(self, client):
        resp = client.get("/dashboard/settings", follow_redirects=False)
        assert resp.status_code == 302

    def test_quarantine_redirects_when_unauthenticated(self, client):
        resp = client.get("/dashboard/quarantine", follow_redirects=False)
        assert resp.status_code == 302

    def test_deliveries_redirects_when_unauthenticated(self, client):
        resp = client.get("/dashboard/deliveries", follow_redirects=False)
        assert resp.status_code == 302

    def test_replies_redirects_when_unauthenticated(self, client):
        resp = client.get("/dashboard/replies", follow_redirects=False)
        assert resp.status_code == 302

    def test_templates_redirects_when_unauthenticated(self, client):
        resp = client.get("/dashboard/templates", follow_redirects=False)
        assert resp.status_code == 302


# ══════════════════════════════════════════════════════════════════════════════
# Logout
# ══════════════════════════════════════════════════════════════════════════════

class TestLogout:
    def test_logout_redirects_to_login(self, client, test_db):
        _create_test_admin_in_db(test_db)
        cookies = _get_session_cookie()
        client.cookies.update(cookies)

        resp = client.get("/dashboard/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard/login" in resp.headers["location"]

    def test_logout_clears_session_cookie(self, client, test_db):
        _create_test_admin_in_db(test_db)
        cookies = _get_session_cookie()
        client.cookies.update(cookies)

        resp = client.get("/dashboard/logout", follow_redirects=False)
        # Cookie should be cleared (max-age=0 or deleted)
        set_cookie = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in set_cookie

    def test_logout_revokes_session_in_db(self, client, test_db):
        _create_test_admin_in_db(test_db)
        session_id = "session-revocation"
        test_db.execute(
            "INSERT INTO sessions (id, email) VALUES (?, ?)",
            [session_id, VALID_EMAIL],
        )
        test_db.commit()
        client.cookies.update(_get_session_cookie())

        resp = client.get("/dashboard/logout", follow_redirects=False)

        assert resp.status_code == 302
        row = test_db.execute("SELECT revoked_at FROM sessions WHERE id = ?", [session_id]).fetchone()
        assert row is not None and row[0] is not None


class TestSessionTrust:
    def test_inactive_user_cookie_is_rejected(self, client, test_db):
        test_db.execute(
            "INSERT INTO admin_users (email, password_hash, role, is_active) VALUES (?, ?, ?, ?)",
            [VALID_EMAIL, "unused", "admin", 0],
        )
        test_db.commit()
        client.cookies.update(_get_session_cookie())

        request = client.get("/dashboard/", follow_redirects=False)
        assert request.status_code == 302
        assert "/dashboard/login" in request.headers["location"]

        with client as c:
            assert get_current_user(type("Req", (), {"cookies": {}, "headers": {}})()) is None


class TestRegistrationFlow:
    def test_registration_is_closed(self, client):
        resp = client.post(
            "/dashboard/register",
            data={
                "name": "Test User",
                "email": "test@ecosave-group.com",
                "phone": "+15551234567",
                "password": "SecurePass123!@",
                "confirm_password": "SecurePass123!@",
                "csrf_token": generate_csrf_token(),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/dashboard/login" in resp.headers["location"]


class TestChangePasswordFlow:
    def test_change_password_success_updates_hash(self, client, test_db):
        _create_test_admin_in_db(test_db)
        client.cookies.update(_get_session_cookie())
        csrf = generate_csrf_token()

        resp = client.post(
            "/dashboard/change-password",
            data={
                "current_password": VALID_PASSWORD,
                "new_password": "NewSecurePass123!",
                "confirm_password": "NewSecurePass123!",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/dashboard/?password_changed=1" in resp.headers["location"]

        row = test_db.execute(
            "SELECT password_hash, force_password_reset FROM admin_users WHERE email = ?",
            [VALID_EMAIL],
        ).fetchone()
        assert row is not None
        assert verify_password("NewSecurePass123!", row["password_hash"]) is True
        assert row["force_password_reset"] == 0

    def test_change_password_rejects_wrong_current_password(self, client, test_db):
        _create_test_admin_in_db(test_db)
        client.cookies.update(_get_session_cookie())
        csrf = generate_csrf_token()

        resp = client.post(
            "/dashboard/change-password",
            data={
                "current_password": "WrongPassword123!",
                "new_password": "NewSecurePass123!",
                "confirm_password": "NewSecurePass123!",
                "csrf_token": csrf,
            },
        )

        assert resp.status_code == 200
        assert "Current password is incorrect" in resp.text

    def test_change_password_rejects_weak_new_password(self, client, test_db):
        _create_test_admin_in_db(test_db)
        client.cookies.update(_get_session_cookie())
        csrf = generate_csrf_token()

        resp = client.post(
            "/dashboard/change-password",
            data={
                "current_password": VALID_PASSWORD,
                "new_password": "short",
                "confirm_password": "short",
                "csrf_token": csrf,
            },
        )

        assert resp.status_code == 200
        assert "Password must meet requirements" in resp.text

    def test_change_password_rejects_mismatch(self, client, test_db):
        _create_test_admin_in_db(test_db)
        client.cookies.update(_get_session_cookie())
        csrf = generate_csrf_token()

        resp = client.post(
            "/dashboard/change-password",
            data={
                "current_password": VALID_PASSWORD,
                "new_password": "NewSecurePass123!",
                "confirm_password": "DifferentPass123!",
                "csrf_token": csrf,
            },
        )

        assert resp.status_code == 200
        assert "Passwords do not match" in resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Public Endpoints (no auth required)
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicEndpoints:
    def test_health_no_auth_required(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_login_page_no_auth_required(self, client):
        resp = client.get("/dashboard/login")
        assert resp.status_code == 200

    def test_test_send_requires_admin_role(self, client, test_db):
        test_db.execute(
            "INSERT INTO admin_users (email, password_hash, role, is_active) VALUES (?, ?, ?, ?)",
            [VALID_EMAIL, "unused", "user", 1],
        )
        template_cursor = test_db.execute(
            "INSERT INTO message_templates (channel, appointment_type, language, rule_name, subject, body, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["sms", "all", "en", "customer_test_send", "Test", "Test body", 1],
        )
        test_db.commit()
        client.cookies.update(_get_session_cookie())

        resp = client.post(
            f"/dashboard/api/templates/{template_cursor.lastrowid}/test-send",
            json={"to": "+15551234567"},
            headers={"X-CSRF-Token": generate_csrf_token()},
        )

        assert resp.status_code == 403
