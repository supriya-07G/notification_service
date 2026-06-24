# EcoSave Notification Service — Actionable Recommendations
**Date:** 2026-06-24  
**Priority:** P0 = Do today | P1 = This week | P2 = Next sprint | P3 = Future

---

## P0 — Critical (Do Today)

### P0-1: Rotate global password and remove hardcoded hash
**Files:** `db/schema.sql:122`, `db/admin_users.py:106`  
**Effort:** 30 minutes  
**Why:** Hash for "EcoSave2026!" is in source code. Anyone with repo access can log in.

Steps:
1. Generate new hash: `python -c "import bcrypt; print(bcrypt.hashpw(b'NEW_PASSWORD', bcrypt.gensalt(12)).decode())"`
2. Remove the `INSERT OR IGNORE INTO system_settings ... dashboard_password_hash` line from `schema.sql`
3. Run `UPDATE system_settings SET value='NEW_HASH' WHERE key='dashboard_password_hash'` on the live DB
4. Replace the fallback literal in `admin_users.py` with `raise RuntimeError("No password hash in DB — run setup")`

---

### P0-2: Fix IP extraction for rate limiting
**File:** `auth/session.py:46-52`  
**Effort:** 1 hour  
**Why:** Attacker forges X-Forwarded-For to bypass 5-attempt lockout.

```python
TRUSTED_PROXY_IPS = {ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1").split(",")}

def _get_client_ip(request: Request) -> str:
    remote = request.client.host if request.client else "unknown"
    if remote in TRUSTED_PROXY_IPS:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return remote
```

Add `TRUSTED_PROXY_IPS=127.0.0.1` to `.env.example`.

---

### P0-3: Add CSRF tokens to all missing mutation endpoints
**File:** `routes/dashboard.py`  
**Effort:** 2 hours  
**Why:** Staff sessions can be hijacked by CSRF from any malicious webpage.

Affected endpoints (add `csrf_token: str = Form(...)` + `validate_csrf_token(request, csrf_token)` to each):
- `POST /appointments/{id}/language`
- `POST /appointments/{id}/no-reminder`
- `POST /appointments/{id}/delete`
- `POST /settings`
- `POST /templates` (create)
- `POST /templates/{id}` (edit)
- `POST /templates/{id}/delete`
- `POST /staff/{id}/toggle`

Add `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">` to each corresponding HTML form.

---

### P0-4: Fix email opt-out enforcement
**File:** `notification_engine.py`  
**Effort:** 1 hour  
**Why:** CAN-SPAM violation — opted-out customers receive email reminders.

Add in `notification_engine.py` before queuing email (find the `INSERT INTO email_queue` block):

```python
# Check opt-out for email channel
row = conn.execute(
    "SELECT 1 FROM opt_outs WHERE phone=? AND channel IN ('email','all')",
    [appt["customer_phone"]],
).fetchone()
if row:
    stats["skipped_opt_out"] += 1
    continue
```

---

## P1 — High Priority (This Week)

### P1-1: Add admin guards to template and alert-settings endpoints
**File:** `routes/dashboard.py`  
**Effort:** 1 hour

Add `Depends(require_admin)` to:
- `POST /templates` (create)
- `POST /templates/{id}` (edit)
- `POST /templates/{id}/delete`
- `GET  /api/settings/alerts`
- `POST /api/settings/alerts`
- `POST /api/templates/{id}/translate`

---

### P1-2: Fix Twilio signature URL reconstruction
**File:** `routes/sms_inbound.py:20-35`, `routes/status_callback.py`  
**Effort:** 30 minutes

Replace header-based URL construction with config-based:
```python
url = f"{config.WEBHOOK_BASE_URL}{request.url.path}"
# No longer trust X-Forwarded-Proto or X-Forwarded-Host for security check
```

---

### P1-3: Mask PII in application logs
**Files:** `adapters/clickup_webhook.py:321-324, 493`  
**Effort:** 1 hour

Add to a shared `utils/log_helpers.py`:
```python
def mask_phone(p: str) -> str:
    return f"***-***-{p[-4:]}" if p and len(p) >= 4 else "***"

def mask_name(n: str) -> str:
    parts = (n or "").split()
    return f"{parts[0][0]}. {parts[-1][0]}." if len(parts) >= 2 else "***"
```

Use in all log statements that currently emit raw phone/name.

---

### P1-4: Fix quarantine resolve control flow
**File:** `routes/dashboard.py:922-963`  
**Effort:** 30 minutes

In the phone validation except block, add `return` before the function reaches the INSERT:
```python
except Exception:
    errors.append("Invalid phone number. Use format: (555) 123-4567")
    return templates.TemplateResponse("dashboard/quarantine.html", {...})
```

---

### P1-5: Fix `updated_by` in settings changes
**File:** `routes/dashboard.py:1024-1057` and anywhere `settings.set()` is called  
**Effort:** 30 minutes

Replace hardcoded `"admin"`:
```python
user = get_session_user(request)
settings.set(key, val, updated_by=user["email"])
```

---

### P1-6: Add startup validation for external credentials
**File:** `webhook_server.py`  
**Effort:** 1 hour

Add a `_validate_config()` function called before `app` starts:
```python
def _validate_config():
    required = {
        "TWILIO_ACCOUNT_SID": config.TWILIO_ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": config.TWILIO_AUTH_TOKEN,
        "TWILIO_SMS_NUMBER": config.TWILIO_SMS_NUMBER,
        "SENDGRID_API_KEY": config.SENDGRID_API_KEY,
        "SENDGRID_FROM_EMAIL": config.SENDGRID_FROM_EMAIL,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
```

---

## P2 — Next Sprint

### P2-1: Replace HTML textarea with safe rich-text editor
**File:** `templates/dashboard/templates.html`  
**Effort:** 4 hours

Replace the raw textarea with a restricted rich-text editor (e.g. Quill.js from CDN). Allow only: bold, italic, line breaks. Strip all HTML tags before saving to DB and display a live preview pane.

**Alternatively:** Switch to plain text templates with `{customer_name}` placeholders only — no HTML at all. Simpler and safer for non-technical staff.

---

### P2-2: Add "Reply to customer" from dashboard
**File:** `routes/dashboard.py`, `templates/dashboard/replies.html`  
**Effort:** 3 hours

Add `POST /replies/{message_id}/reply` endpoint:
```python
@router.post("/replies/{message_id}/reply")
async def reply_to_customer(message_id: int, body: str = Form(...), _=Depends(require_admin)):
    # look up phone from inbound_messages
    # call channels/twilio_sms.send_sms(to=phone, body=body)
    # log to notification_attempts
```

Add a small reply form on the replies page.

---

### P2-3: Add `GET /health` endpoint
**File:** `webhook_server.py`  
**Effort:** 30 minutes

```python
@app.get("/health")
def health():
    try:
        conn = db.init.get_connection()
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)
```

---

### P2-4: Use absolute DB path
**File:** `config.py:14`  
**Effort:** 15 minutes

```python
import pathlib
_base = pathlib.Path(__file__).parent
DB_PATH = os.getenv("DB_PATH", str(_base / "storage.sqlite"))
```

---

### P2-5: Add cron lock files
**File:** all cron scripts  
**Effort:** 2 hours

Wrap each cron script entry point with a file lock:
```python
import fcntl, sys

lock = open(f"/tmp/notification_{__name__}.lock", "w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    sys.exit(0)  # Another instance is running
```

On Windows use `msvcrt.locking` or a PID file approach.

---

### P2-6: Remove committed binary and real IDs from source
**Files:** `.gitignore`, `config.py`  
**Effort:** 30 minutes

1. Add `ngrok.exe` to `.gitignore`
2. Remove `CLICKUP_LIST_ID` default from `config.py` (require it in `.env`)
3. Consider moving Google Calendar IDs out of `config.py` source into `.env` only

---

## P3 — Future Enhancements

### P3-1: Single orchestrator to replace 4 cron jobs
Replace the 4 separate cron jobs with a single `scheduler.py` using `APScheduler`:
```python
from apscheduler.schedulers.blocking import BlockingScheduler
scheduler = BlockingScheduler()
scheduler.add_job(calendar_sync.run, 'interval', minutes=15)
scheduler.add_job(notification_engine.run, 'interval', minutes=30)
scheduler.add_job(reply_processor.run, 'interval', minutes=5)
scheduler.add_job(sendgrid_email.run, 'cron', minute='5,35')
scheduler.start()
```

This allows centralized error handling, logging, and health monitoring.

---

### P3-2: Audit log table
Add an `audit_log` table and write to it on every state-changing action:
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    performed_by TEXT,
    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    detail_json TEXT
);
```

---

### P3-3: SendGrid webhook signature validation
Enable SendGrid Event Webhook Verification and validate `X-Twilio-Email-Event-Webhook-Signature` in `routes/sendgrid_status.py`.

---

### P3-4: SQLite backup strategy
Add a daily backup job:
```python
import shutil, datetime
shutil.copy("storage.sqlite", f"backups/storage_{datetime.date.today()}.sqlite")
```

Retain last 30 days. Consider encrypting at rest if customer PII is stored.

---

### P3-5: Notification failure alerting
In `notification_engine.py`, if failure rate in a single run exceeds a threshold (e.g. 3+ failures), send a Discord/email alert to admin:
```python
if stats["failed"] >= 3:
    send_admin_alert(f"Notification engine: {stats['failed']} failures in this run")
```
