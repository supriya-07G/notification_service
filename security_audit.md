# EcoSave Notification Service — Security Audit
**Date:** 2026-06-24  
**Standard:** OWASP Top 10 + PII / CAN-SPAM / TCPA basics

---

## 1. Authentication

### Current State
- Global shared password stored as bcrypt hash in `system_settings` table
- Per-staff login added (Phase 4): `admin_users` table, bcrypt-hashed passwords
- Session via signed cookie (`itsdangerous.TimestampSigner`)
- Brute-force lockout: 5 attempts per IP per 15 minutes

### Issues

| ID | Severity | Finding |
|----|----------|---------|
| A1 | CRITICAL | Hardcoded bcrypt hash `$2b$12$.FLmAAt0...` for "EcoSave2026!" in `db/schema.sql:122` and as fallback in `db/admin_users.py:106`. Anyone with repo access can log in. |
| A2 | CRITICAL | X-Forwarded-For header trusted without verification (`auth/session.py:46-52`). Attacker can bypass lockout by rotating this header. |
| A3 | MEDIUM | No password expiry — staff passwords never forced to rotate |
| A4 | LOW | Login failure does not increment attempt counter for non-existent usernames, only for existing ones — enables username enumeration via timing difference |

### Remediation

**A1:** Remove hash from schema. On first run, require password to be set via environment variable:
```python
# db/schema.sql — remove hardcoded INSERT
# config.py — add DASHBOARD_INITIAL_PASSWORD_HASH env var
# db/admin_users.py — raise RuntimeError if no hash found instead of falling back
```

**A2:** Only trust X-Forwarded-For from known proxy IPs:
```python
TRUSTED_PROXIES = {"127.0.0.1"}  # add your nginx IP

def _get_client_ip(request: Request) -> str:
    remote = request.client.host if request.client else "unknown"
    if remote in TRUSTED_PROXIES:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return remote
```

---

## 2. Authorization (Role-Based Access)

### Current State
- Two roles: `admin` and `staff`
- `require_auth()` dependency checks session exists
- Some routes have explicit `if user["role"] != "admin": raise 403`

### Issues

| ID | Severity | Finding |
|----|----------|---------|
| Z1 | HIGH | `POST /templates` (create template) — no admin check. Staff can add/change message templates sent to customers. `routes/dashboard.py:1094` |
| Z2 | HIGH | `GET/POST /api/settings/alerts` — no admin check. Staff can change Discord/SMS/Email alert configuration. `routes/dashboard.py:1373, 1397` |
| Z3 | HIGH | `POST /api/templates/{id}/translate` — no admin check. `routes/dashboard.py:1334` |
| Z4 | MEDIUM | `POST /templates/{id}` (edit template) — no admin check. `routes/dashboard.py:1125` |
| Z5 | MEDIUM | `POST /templates/{id}/delete` — no admin check. `routes/dashboard.py:1151` |

### Remediation

Add the admin guard dependency to every mutation route that changes system-wide config:

```python
def require_admin(request: Request):
    user = get_session_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# Apply to affected routes:
@router.post("/templates")
async def create_template(request: Request, _admin=Depends(require_admin), ...):
```

---

## 3. CSRF Protection

### Current State
- CSRF token generated and validated on: login form, password change form
- Implementation uses double-submit cookie pattern via `auth/csrf.py`

### Issues

| ID | Severity | Endpoint lacking CSRF |
|----|----------|-----------------------|
| R1 | HIGH | `POST /appointments/{id}/language` |
| R2 | HIGH | `POST /appointments/{id}/no-reminder` |
| R3 | HIGH | `POST /appointments/{id}/delete` |
| R4 | HIGH | `POST /settings` (global settings save) |
| R5 | HIGH | `POST /templates` (create) |
| R6 | HIGH | `POST /templates/{id}` (edit) |
| R7 | HIGH | `POST /templates/{id}/delete` |
| R8 | HIGH | `POST /staff/{id}/toggle` |

**Attack scenario:** A staff member visits a malicious webpage while logged in. The page silently POSTs to `/appointments/123/delete`. Because there is no CSRF token, the delete succeeds.

### Remediation

1. Add hidden CSRF input to every form:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
```

2. Add CSRF validation to every POST handler:
```python
from auth.csrf import validate_csrf_token
...
csrf_token: str = Form(...)
validate_csrf_token(request, csrf_token)
```

---

## 4. Webhook Security

### Twilio Webhooks

| Check | Status | Notes |
|-------|--------|-------|
| X-Twilio-Signature validated | ✅ Yes | `routes/sms_inbound.py`, `routes/status_callback.py` |
| Idempotency enforced | ❌ No | No deduplication table for Twilio events |
| URL reconstruction safe | ⚠️ Partial | Trusts X-Forwarded-Proto/Host — see below |

**Issue (MEDIUM):** Signature validation constructs the URL from headers:
```python
scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
host   = request.headers.get("x-forwarded-host", request.headers.get("host", ...))
```
If an attacker can influence these headers the reconstructed URL differs from what Twilio signed, potentially causing valid webhooks to fail or allowing crafted requests to pass.

**Fix:** Use `WEBHOOK_BASE_URL` from config as the authoritative base, ignore forwarded headers for signature validation:
```python
url = f"{config.WEBHOOK_BASE_URL}{request.url.path}"
```

### ClickUp Webhooks

| Check | Status | Notes |
|-------|--------|-------|
| HMAC signature validated | ✅ Yes | `adapters/clickup_webhook.py` |
| Idempotency enforced | ✅ Yes | `webhook_events` table with UNIQUE on event_id |
| Duplicate delivery handled | ✅ Yes | Returns 200 on replay |

### SendGrid Status Webhooks

| Check | Status | Notes |
|-------|--------|-------|
| Signed webhook key validated | ❌ No | No SendGrid webhook signature check |
| Idempotency enforced | ❌ No | No deduplication |

**Fix:** Enable SendGrid's signed webhooks (Event Webhook Verification) and validate `X-Twilio-Email-Event-Webhook-Signature` header.

---

## 5. SQL Injection

| Status | Overall |
|--------|---------|
| ✅ Safe | All queries use `?` parameterized placeholders |

No raw string concatenation found in SQL queries. The single edge case (`LIKE ?` with `%` suffix in `routes/sendgrid_status.py`) is parameterized, though the wildcard match logic should be changed to an exact `=` for correctness.

---

## 6. XSS (Cross-Site Scripting)

| Status | Overall |
|--------|---------|
| ✅ Mostly Safe | Jinja2 auto-escapes by default |

**Risk area:** Message templates stored in the DB and rendered into notification SMS/email bodies. If a staff member enters `{{` in a template body, Jinja2 template rendering in `notification_engine.py` could fail or be exploited if using `render_template_string()` with unsanitized content.

**Check:** Confirm template rendering uses a sandboxed environment or escape all `{{` in user-submitted template content.

---

## 7. Session Management

| Check | Status | Notes |
|-------|--------|-------|
| Signed session cookie | ✅ Yes | `itsdangerous.TimestampSigner` |
| HttpOnly flag | ✅ Yes | `auth/session.py` |
| SameSite=Lax | ✅ Yes | Default FastAPI behavior |
| Secure flag | ⚠️ Config | Controlled by `SESSION_SECURE_COOKIE` env var — must be `true` in production |
| Session expiry | ✅ Yes | `SESSION_MAX_AGE_SECONDS` (default 8h) |
| Session invalidation on logout | ✅ Yes | Cookie cleared on logout |

**Risk:** `SESSION_SECURE_COOKIE` is read as a string. The value `"false"` (case-insensitive) disables the Secure flag. Ensure production `.env` sets this to `"true"`.

---

## 8. PII and Data Privacy

### CAN-SPAM / TCPA Compliance

| Requirement | Status |
|-------------|--------|
| SMS opt-out (STOP) handled | ✅ Yes — `opt_outs` table |
| Email opt-out handled | ❌ No — email channel does not check `opt_outs` |
| Opt-out respected before send | ⚠️ SMS only |
| STOP confirmation message | ✅ Yes — auto-reply sent |

**Critical:** Fix email opt-out check immediately. Sending marketing/reminder emails to opted-out contacts violates CAN-SPAM.

### Log PII

**Issue (HIGH):** Phone numbers and customer names are written directly to application logs:
```python
# adapters/clickup_webhook.py:321
logger.warning("Quarantined ClickUp task %s: %s (name=%s, phone=%s)",
    data["task_id"], reason, data.get("customer_name"), data.get("customer_phone"))
```

Logs are typically retained longer than customer data, are often forwarded to aggregation tools, and may not have the same access controls as the database.

**Fix:** Mask PII before logging:
```python
def mask_phone(p: str) -> str:
    return f"***-***-{p[-4:]}" if p and len(p) >= 4 else "***"

def mask_name(n: str) -> str:
    parts = n.split() if n else []
    return f"{parts[0][0]}. {parts[-1][0]}." if len(parts) >= 2 else "***"
```

---

## 9. Secret Management

| Secret | Source | Risk |
|--------|--------|------|
| `SESSION_SECRET_KEY` | `.env` only | ✅ Good — validated at startup (min 32 chars) |
| `TWILIO_AUTH_TOKEN` | `.env` only | ✅ Good |
| `SENDGRID_API_KEY` | `.env` only | ✅ Good |
| `CLICKUP_WEBHOOK_SECRET` | `.env` only | ✅ Good |
| `dashboard_password_hash` | Schema SQL + DB | ❌ Bad — hardcoded in schema |
| `CLICKUP_LIST_ID` | `config.py` default | ⚠️ Real production ID in source |
| Google Calendar IDs | `config.py` | ⚠️ Real calendar IDs in source |

**`.env` is in `.gitignore`** — confirmed. The risk is the defaults in `config.py` which expose real IDs.

---

## 10. Audit Logging

| Action | Logged | Who |
|--------|--------|-----|
| Login success | ✅ Yes | username + IP |
| Login failure | ✅ Yes | IP + attempt count |
| Logout | ✅ Yes | |
| Settings change | ⚠️ Partial | `updated_by` always `"admin"` not actual user |
| Template change | ❌ No | |
| Appointment delete | ❌ No | |
| Staff add/remove | ❌ No | |
| Quarantine resolve | ⚠️ Partial | `resolved_by` stored |
| Notification sent | ✅ Yes | `notification_attempts` table |
| Opt-out received | ✅ Yes | `opt_outs` table |

**Recommendation:** Add an `audit_log` table with `(id, action, entity_type, entity_id, performed_by, performed_at, detail_json)` and write to it on every state-changing action.
