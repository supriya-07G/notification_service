# EcoSave Notification Service — Folder Structure
**Date:** 2026-06-24

---

## Root Directory

| File / Folder | Purpose |
|---------------|---------|
| `webhook_server.py` | FastAPI application entry point. Mounts all routers, serves static files and templates, starts uvicorn. |
| `notification_engine.py` | Core scheduler logic. Reads upcoming appointments, checks rules (72h/24h/2h), deduplicates, sends SMS via Twilio, queues email. Run every 30 min. |
| `calendar_sync.py` | Pulls events from all Google Calendars configured in `.env`. Parses customer name/phone/language from event descriptions. Inserts to `appointments` or `appointment_quarantine`. Run every 15 min. |
| `config.py` | Central configuration. All values read from `.env` via `python-dotenv`. Defines `NOTIFICATION_RULES`, `CALENDAR_SOURCE_MAP`, all API credentials. |
| `requirements.txt` | Python package dependencies. |
| `.env.example` | Template showing all required environment variables. Do not store real values here. |
| `CLAUDE.md` | Claude Code project instructions and phase tracker. |
| `ngrok.exe` | ⚠️ Should not be committed. Development tunnel binary for exposing local server. |
| `storage.sqlite` | SQLite database file (not committed — gitignored). Contains all live data. |

---

## `auth/`

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `session.py` | Session cookie creation, validation, and per-IP rate-limit lockout. Defines `require_auth()` FastAPI dependency. |
| `csrf.py` | CSRF token generation (double-submit cookie pattern) and validation. |

---

## `db/`

| File | Purpose |
|------|---------|
| `schema.sql` | Full SQLite DDL. All `CREATE TABLE IF NOT EXISTS`, indexes, and seed data. Safe to re-run. |
| `init.py` | `get_connection()` — opens SQLite, enables WAL mode, foreign keys, row_factory. |
| `migrate.py` | CLI script to apply `schema.sql` against `storage.sqlite`. Run once on deployment. |
| `settings.py` | `get(key)` / `set(key, value, updated_by)` helpers for `system_settings` table. |
| `templates.py` | CRUD helpers for `notification_templates` table (list, get, update). |
| `admin_users.py` | Staff login management: create user, authenticate, list, toggle active/inactive. Password strength validation. |

---

## `channels/`

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `twilio_sms.py` | `send_sms(to, body, conn)` — sends via Twilio REST. `can_send(phone, conn)` — checks opt-outs. |
| `sendgrid_email.py` | Cron worker. Reads `email_queue`, renders templates, sends via SendGrid API. Updates queue status on success/failure. Run every 5-35 min. |

---

## `routes/`

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `dashboard.py` | All staff-facing routes: login, overview, appointments, deliveries, quarantine, replies, settings, templates, staff management. Also JSON API endpoints for AJAX actions. |
| `sms_inbound.py` | `POST /webhooks/twilio/inbound`. Validates Twilio signature, handles STOP words (writes to `opt_outs`), saves all inbound messages to `inbound_messages`. |
| `status_callback.py` | `POST /webhooks/twilio/status`. Validates Twilio signature, updates `notification_attempts.status` from Twilio delivery receipts. |
| `sendgrid_status.py` | `POST /webhooks/sendgrid/status`. Processes SendGrid event webhooks (delivered, bounce, open, click). Updates `email_queue` and `notification_attempts`. ⚠️ No signature validation. |
| `clickup_webhook.py` | `POST /webhooks/clickup`. Validates HMAC signature, delegates to `adapters/clickup_webhook.py`. |

---

## `adapters/`

| File | Purpose |
|------|---------|
| `clickup_webhook.py` | Parses ClickUp task webhook payloads. Maps custom field UUIDs to values. Extracts customer name, phone, email, appointment type, and date from task fields. Inserts to `appointments` or `appointment_quarantine`. |

---

## `workers/`

| File | Purpose |
|------|---------|
| `reply_processor.py` | Cron worker. Reads unprocessed rows from `inbound_messages`. Classifies as YES / NO / STOP / other. Sends staff alerts via Discord, SMS, email. Marks replies processed. Run every 5 min. |

---

## `templates/dashboard/`

| File | Purpose |
|------|---------|
| `base.html` | Layout template: Bootstrap navbar, flash messages, scripts. All pages extend this. |
| `login.html` | Staff login form. CSRF token included. |
| `index.html` | Dashboard home: KPI stats (sent today, pending, failures, opt-outs). |
| `appointments.html` | Appointment list with search, filter by date/source/status. Inline actions (delete, override language, suppress reminder). |
| `deliveries.html` | Notification delivery log. Shows all attempts with status, channel, rule. Export to CSV. |
| `quarantine.html` | Appointments with data quality issues. Resolve form allows staff to correct phone/name before adding to active table. |
| `replies.html` | Inbound SMS replies from customers. Shows classification (YES/NO/STOP/unknown). |
| `settings.html` | System settings panel (admin only): kill switch, quiet hours, alert contacts, API config. |
| `templates.html` | Notification template editor (admin). Manage SMS/email templates per rule and language. ⚠️ Shows raw HTML — needs rich-text editor. |
| `staff.html` | Staff management (admin). Add staff, toggle active/inactive. |

---

## `static/`

| File | Purpose |
|------|---------|
| `logo_clean.png` | EcoSave logo (clean version) |
| `logo_partner.png` | Partner/co-branding logo |
| `custom.css` | Custom CSS overrides on top of Bootstrap |
| `template-editor.js` | JavaScript for template editor — variable insertion helpers |

---

## `tests/`

| File | Purpose |
|------|---------|
| `conftest.py` | Pytest fixtures: in-memory SQLite, test FastAPI client, mock Twilio/SendGrid |
| `test_auth.py` | Login, logout, rate limiting, session expiry |
| `test_calendar_sync.py` | Google Calendar parsing, quarantine logic |
| `test_dashboard.py` | Dashboard routes, CRUD operations |
| `test_e2e_smoke.py` | End-to-end smoke test: create appointment → trigger engine → verify attempt |
| `test_engine.py` | Notification engine: rule matching, dedup, quiet hours |
| `test_reply_processor.py` | Reply classification, opt-out, alert sending |
| `test_settings.py` | Settings get/set, kill switch behavior |
| `test_templates.py` | Template CRUD, rendering |
| `test_webhooks.py` | Twilio/SendGrid/ClickUp webhook handlers, signature validation |

---

## `deployment/`

| File | Purpose |
|------|---------|
| `nginx_notification.conf` | Nginx reverse proxy config. Routes HTTPS → uvicorn on port 8096. Sets proxy headers. |
| `notification_service.cron` | Linux/macOS cron entries for all 4 scheduled tasks. |
| `README.md` | Deployment instructions. |

---

## `systemd/`

| File | Purpose |
|------|---------|
| `notification-webhooks.service` | systemd unit file to run uvicorn as a managed service. |

---

## Uncommitted / Untracked Files

These files exist on disk but are not in git (`??` status):

| File | Notes |
|------|-------|
| `adapters/` | Tracked by git but some files may be untracked |
| `utils/` | Utility helpers — contents not reviewed in git |
| `routes/clickup_webhook.py` | ClickUp webhook route — appears to be outside main `routes/` |
| `create_admin.py` | One-off script to create admin user |
| `wipe_data.py` | ⚠️ Dangerous — wipes database. Should be restricted / require confirmation |
| `sync_existing_tasks.py` | One-off sync script |
| `patch.py` | Ad-hoc patch script — unclear purpose |
| `get_fields.py` | Helper to discover ClickUp custom field IDs |
| `run_alert_migration.py` | DB migration for alert system |
| `_run_staff_migration.py` | DB migration for staff roles |
| `db/migrate_clickup.sql` | ClickUp-specific schema additions |
| `db/migrate_staff_roles.sql` | Staff roles schema migration |
| `static/custom.css` | CSS overrides |
| `static/template-editor.js` | Template editor JavaScript |
| `templates/dashboard/staff.html` | Staff management template |
