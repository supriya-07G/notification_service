# EcoSave Notification Service — Audit Report
**Date:** 2026-06-24  
**Auditor:** Senior Software Architect / Security Auditor  
**Scope:** Full codebase review — architecture, security, bugs, code quality

---

## Executive Summary

| Metric | Score |
|--------|-------|
| Overall System Health | 6 / 10 |
| Security Posture | 5 / 10 |
| Code Quality | 6 / 10 |
| Documentation | 5 / 10 |
| Maintainability | 6 / 10 |

**Critical Issues Found:** 3  
**High-Severity Issues:** 7  
**Medium-Severity Issues:** 8  
**Code Quality Issues:** 12  
**Missing Features:** 6  

**Bottom line:** The core notification logic is solid and the SQLite schema is well-designed. The danger areas are authentication (shared global password, forged IP rate-limit bypass), missing CSRF protection on most mutation endpoints, and PII leaking into logs. None of these require major rewrites — they are targeted, surgical fixes. The system is safe to operate at current scale while fixes are applied, but the shared password must be rotated today.

---

## Critical Issues (Must Fix Immediately)

| # | Issue | File | Impact |
|---|-------|------|--------|
| C1 | Hardcoded bcrypt hash of default password in schema.sql | `db/schema.sql:122` | Any attacker who sees the codebase can log into the dashboard |
| C2 | X-Forwarded-For header trusted blindly for rate limiting | `auth/session.py:46-52` | 5-attempt lockout completely bypassable with forged header |
| C3 | No startup validation of external API credentials | `config.py`, `webhook_server.py` | Twilio/SendGrid silently misconfigured; notifications fail without alert |

---

## High-Severity Issues

| # | Issue | File | Impact |
|---|-------|------|--------|
| H1 | CSRF tokens missing on ~8 mutation endpoints | `routes/dashboard.py` | Malicious page can trigger deletions, setting changes, template edits |
| H2 | Email channel has no opt-out check | `notification_engine.py`, `channels/sendgrid_email.py` | Opted-out customers still receive email reminders — CAN-SPAM/TCPA risk |
| H3 | Staff can modify templates and alert settings (no admin check) | `routes/dashboard.py:1094, 1334, 1373, 1397` | Any staff login can change message content sent to all customers |
| H4 | Twilio signature URL reconstructed from untrusted headers | `routes/sms_inbound.py:20-35`, `routes/status_callback.py` | Webhook spoofing possible via X-Forwarded-Proto/Host manipulation |
| H5 | Hardcoded fallback bcrypt hash in admin_users.py | `db/admin_users.py:106` | If settings row is deleted, system silently falls back to known password |
| H6 | Phone number validation may not block execution on parse error | `routes/dashboard.py:922-963` | Quarantine resolve could write invalid phone to appointments table |
| H7 | PII (customer names, phone numbers) written into application logs | `adapters/clickup_webhook.py:321-324, 493` | Logs become a PII data store with no retention controls |

---

## Medium-Severity Issues

| # | Issue | File | Impact |
|---|-------|------|--------|
| M1 | No rate limiting on API mutation endpoints | `routes/dashboard.py:1398` | Staff can spam template or settings changes |
| M2 | Race condition in notification deduplication | `notification_engine.py:105-124` | Two concurrent engine runs could send duplicate notifications |
| M3 | No SendGrid webhook deduplication | `routes/sendgrid_status.py` | Retry from SendGrid updates delivery status twice |
| M4 | Email address validation regex too permissive | `routes/dashboard.py:1435` | `test@.com` or other malformed addresses accepted |
| M5 | Calendar sync silently drops events with parse failures | `calendar_sync.py:95-119` | Appointments lost with no trace — staff not alerted |
| M6 | SendGrid timeout not set | `channels/sendgrid_email.py:75` | Send call can hang indefinitely, blocking the worker |
| M7 | No audit trail when settings are changed | `routes/dashboard.py:1024-1057` | `updated_by` is always hardcoded `"admin"` not actual user |
| M8 | No spam/unknown-sender filtering on inbound SMS | `routes/sms_inbound.py` | All SMS to Twilio number logged, including spam and wrong numbers |

---

## Bug Report

### Functional Bugs

**BUG-1: Opt-out not enforced for email**  
`notification_engine.py` checks `can_send()` for SMS but never for email. A customer who texted STOP will still receive email reminders.  
*Fix:* Add `can_send_email(phone, conn)` check before inserting into `email_queue`.

**BUG-2: Quarantine resolve phone validation control flow**  
`routes/dashboard.py:922-963` — exception on phone parse re-renders the form but does NOT `return`. Code continues to the INSERT block. If `formatted_phone` is unset the INSERT will use an empty string.  
*Fix:* Add `return` inside the except block.

**BUG-3: Settings `updated_by` always "admin"**  
`db/settings.py` — `set()` always records `updated_by="admin"`. After individual staff login was added this became wrong — the actual logged-in user is never recorded.  
*Fix:* Pass `request.session["user"]["email"]` as `updated_by`.

**BUG-4: Calendar sync all-day events get midnight time**  
`calendar_sync.py` — events with only a `date` (no `dateTime`) are given midnight in `TZ`. For work appointments this creates a 2 AM reminder trigger, which fires outside quiet hours logic at 72 h.  
*Fix:* Log a warning and skip all-day events, or default to 08:00 with a comment.

**BUG-5: `LIKE` wildcard match on `sg_message_id`**  
`routes/sendgrid_status.py:38-41` — query uses `LIKE ?` with `%` suffix. If two message IDs share a common prefix (unlikely but possible with custom message IDs), the wrong record could be updated.  
*Fix:* Use exact `=` match on the full stored ID.

### UI / UX Issues

**UX-1: Template editor shows raw HTML to non-technical staff**  
`templates/dashboard/templates.html` — the template body textarea contains raw HTML markup. Staff are not developers; they will accidentally break tags.  
*Fix:* Add a simple rich-text editor (e.g. Quill.js CDN) or restrict HTML to a safe subset with a preview pane.

**UX-2: No reply-to-customer feature from dashboard**  
Staff can see inbound SMS replies but cannot reply from the dashboard. They must use their phone.  
*Fix:* Add a "Reply" button on the replies page that POSTs to a `/send-reply` endpoint using Twilio's API.

**UX-3: Appointment type shows UUID instead of human name**  
Known from previous work — ClickUp field mapping returns UUID for appointment type rather than label text.  
*Fix:* Map UUID → label in `adapters/clickup_webhook.py` using the ClickUp `option.name` field.

**UX-4: Quarantine page lacks context for resolver**  
The quarantine detail view doesn't show the raw ClickUp task URL, making it hard for staff to look up the original task.  
*Fix:* Store ClickUp task URL in quarantine row and display it on resolve form.

---

## Common Sense Mistakes

### Architecture

1. **Four separate cron jobs** where a single orchestrator script would be cleaner. `calendar_sync`, `notification_engine`, `reply_processor`, and `sendgrid_email` each open their own DB connection and run independently. A missed cron job is invisible — no alerting.

2. **SQLite on a Windows dev machine** is fine for current scale but `DB_PATH` defaults to `./storage.sqlite` relative to CWD. If the server is started from a different directory the DB path silently changes and a blank database is created.  
   *Fix:* Use an absolute path derived from `__file__`.

3. **`ngrok.exe` committed to git repository** — binary should be in `.gitignore` and never checked in.

4. **No health-check endpoint** — there is no `GET /health` route. A load balancer or monitoring tool has no way to verify the server is alive.

### Configuration

5. **`WEBHOOK_BASE_URL` defaults to `https://hooks.yourdomain.com`** — this is a placeholder that will cause Twilio signature validation to fail on first deployment if not overridden.

6. **`SESSION_SECURE_COOKIE` is a string `"true"`, not a boolean** — the code at `auth/session.py` must parse it explicitly. If someone sets it to `"True"` (capital T) the behavior may differ.

7. **`CLICKUP_LIST_ID` hardcoded default `901317175958` in `config.py:50`** — this is a real production ID in source code. It should be environment-variable only with no default.

### Deployment

8. **No systemd `Restart=on-failure`** check in `systemd/notification-webhooks.service` — if the FastAPI process crashes it may not auto-restart.

9. **Cron jobs have no lock files** — if a notification engine run takes longer than 30 minutes the next cron fires and two instances run simultaneously (the race condition in M2 above).

10. **No log rotation configured** — Python `logging` defaults to stdout; on Windows this may fill disk or be lost on reboot.

### Code

11. **`import math` and `import csv, io, json` inside function body** (`routes/dashboard.py`) — unused imports left in function scope, suggesting dead code.

12. **Duplicate regex patterns** defined in both `calendar_sync.py` and `routes/dashboard.py` — single source of truth needed.

13. **No type checking enforced** — no `mypy` or `pyright` in CI, no `py.typed` marker. Type hints present but unenforced.

---

## Positive Findings

The following are done correctly and should be maintained:

- ✅ All SQL queries use parameterized `?` placeholders — no string concatenation
- ✅ Passwords hashed with bcrypt (12 rounds)
- ✅ Session tokens signed with `itsdangerous` — cannot be forged
- ✅ `PRAGMA foreign_keys=ON` enforced per connection
- ✅ Email queued and sent async — dashboard is never blocked
- ✅ UNIQUE constraint on `notification_attempts` as primary deduplication guard
- ✅ Appointment quarantine prevents bad data reaching notification engine
- ✅ Twilio status callbacks validated with signature (modulo URL reconstruction issue)
- ✅ Per-staff login with bcrypt hashes implemented (Phase 4)
- ✅ Discord alerting for STOP/NO replies implemented

---

## Roadmap

### Phase 1 — Critical Fixes (Do Today)
- [ ] Rotate global dashboard password, remove hash from schema.sql
- [ ] Fix X-Forwarded-For: only trust from known reverse proxy IPs
- [ ] Add CSRF tokens to all 8 missing mutation endpoints
- [ ] Add email opt-out check before queuing email notifications
- [ ] Fix quarantine resolve control flow (add `return` in except block)

### Phase 2 — High Priority (This Week)
- [ ] Add admin-only guards to template and alert-settings endpoints
- [ ] Fix Twilio signature URL: use configured `WEBHOOK_BASE_URL` not reconstructed headers
- [ ] Mask PII in logs (phone → last 4 digits, name → initials)
- [ ] Pass actual logged-in user to `settings.set(updated_by=...)`
- [ ] Add startup validation for Twilio/SendGrid credentials

### Phase 3 — Improvements (Next Sprint)
- [ ] Replace raw HTML textarea in template editor with safe rich-text editor
- [ ] Add "Reply to customer" from dashboard
- [ ] Add `GET /health` endpoint
- [ ] Add cron lock files to prevent concurrent runs
- [ ] Fix `DB_PATH` to use absolute path from `__file__`
- [ ] Add `ngrok.exe` to `.gitignore`
- [ ] Add `CLICKUP_LIST_ID` environment-variable requirement (remove default)

### Phase 4 — Enhancements (Future)
- [ ] Replace 4 cron jobs with single orchestrator with internal scheduler
- [ ] Add SendGrid webhook deduplication
- [ ] Add monitoring/alerting on notification failure rate
- [ ] Add SQLite WAL backup strategy
