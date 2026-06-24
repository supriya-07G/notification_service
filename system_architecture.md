# EcoSave Notification Service — System Architecture
**Date:** 2026-06-24

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Web Framework | FastAPI (ASGI) |
| WSGI Server | Uvicorn |
| Database | SQLite 3 (WAL mode, file: `storage.sqlite`) |
| Template Engine | Jinja2 |
| Frontend CSS | Bootstrap 5, Inter font (CDN) |
| SMS | Twilio REST API |
| Email | SendGrid REST API |
| Calendar Source A | Google Calendar API (service account) |
| Calendar Source B | ClickUp Webhooks (HVAC list) |
| Alerting | Discord Webhook, Twilio SMS |
| Auth | bcrypt + itsdangerous signed cookies |
| HTTP client | httpx (async), requests (sync scripts) |
| Scheduling | Windows Task Scheduler / cron |
| Tunnel (dev) | ngrok |

---

## Folder Structure

```
notification_service/
├── webhook_server.py          # FastAPI app — mounts all routers, starts server
├── notification_engine.py     # Core: reads appointments, queues and sends notifications
├── calendar_sync.py           # Pulls events from Google Calendar → appointments table
│
├── config.py                  # All settings read from .env via python-dotenv
├── requirements.txt
├── .env.example               # Template for required environment variables
│
├── auth/
│   ├── session.py             # Session creation, validation, rate-limit lockout
│   └── csrf.py                # CSRF token generation and validation
│
├── db/
│   ├── schema.sql             # Full SQLite DDL — tables, indexes, seed data
│   ├── init.py                # Applies schema.sql, sets PRAGMA options
│   ├── migrate.py             # Run to initialise DB on new deployment
│   ├── settings.py            # CRUD helpers for system_settings table
│   ├── templates.py           # CRUD helpers for notification_templates table
│   └── admin_users.py         # Staff login: create, authenticate, list, toggle
│
├── channels/
│   ├── sendgrid_email.py      # Drains email_queue → SendGrid API (cron every 30 min)
│   └── twilio_sms.py          # send_sms() helper + can_send() opt-out check
│
├── routes/
│   ├── __init__.py
│   ├── dashboard.py           # All staff-facing HTML routes + API endpoints
│   ├── sms_inbound.py         # POST /webhooks/twilio/inbound (Twilio callback)
│   ├── status_callback.py     # POST /webhooks/twilio/status (delivery status)
│   └── sendgrid_status.py     # POST /webhooks/sendgrid/status (delivery status)
│
├── adapters/
│   └── clickup_webhook.py     # Parses ClickUp task events → appointments table
│
├── workers/
│   └── reply_processor.py     # Classifies inbound replies, sends staff alerts
│
├── templates/dashboard/
│   ├── base.html              # Layout: nav, flash messages, Bootstrap
│   ├── login.html
│   ├── index.html             # Overview / dashboard home
│   ├── appointments.html      # Appointment list with search/filter
│   ├── deliveries.html        # Notification delivery log
│   ├── quarantine.html        # Appointments with data issues
│   ├── replies.html           # Customer SMS replies
│   ├── settings.html          # System settings (admin)
│   ├── templates.html         # Notification template editor (admin)
│   └── staff.html             # Staff management (admin)
│
├── static/
│   ├── logo_clean.png
│   ├── logo_partner.png
│   ├── custom.css
│   └── template-editor.js
│
├── tests/
│   ├── conftest.py            # Pytest fixtures, test DB setup
│   ├── test_auth.py
│   ├── test_calendar_sync.py
│   ├── test_dashboard.py
│   ├── test_e2e_smoke.py
│   ├── test_engine.py
│   ├── test_reply_processor.py
│   ├── test_settings.py
│   ├── test_templates.py
│   └── test_webhooks.py
│
├── deployment/
│   ├── nginx_notification.conf   # Nginx reverse proxy config
│   ├── notification_service.cron # Linux cron entries
│   └── README.md
│
└── systemd/
    └── notification-webhooks.service  # systemd unit for FastAPI process
```

---

## Data Flow Diagrams

### Flow 1: Appointment from Google Calendar

```
Google Calendar
     │
     │ (every 15 min cron)
     ▼
calendar_sync.py
     │  reads events via Google Calendar API
     │  parses: customer name, phone, email, date, language
     │  validates phone (E.164), checks for [NO REMINDER] tag
     ▼
appointments table          appointment_quarantine table
(valid records)             (missing phone / invalid phone /
                             ambiguous name / parse error)
```

### Flow 2: Appointment from ClickUp Webhook

```
ClickUp (HVAC list)
     │
     │ POST /webhooks/clickup  (task created / updated)
     ▼
adapters/clickup_webhook.py
     │  validates HMAC signature
     │  checks webhook_events for duplicates
     │  extracts fields using CLICKUP_FIELD_* UUIDs from config
     │  maps Scope-of-Work UUID → appointment_type label
     │  formats phone to E.164
     ▼
appointments table          appointment_quarantine table
```

### Flow 3: Sending Notifications

```
(every 30 min cron)
     │
     ▼
notification_engine.py
     │  queries appointments WHERE appointment_at is upcoming
     │  for each NOTIFICATION_RULES (72h, 24h, 2h):
     │    checks no_reminder flag
     │    checks quiet hours (22:00–08:00, except 2h rule)
     │    checks opt_outs table (SMS only — BUG: not for email)
     │    tries INSERT INTO notification_attempts (dedup via UNIQUE)
     │    if new attempt:
     │      SMS → channels/twilio_sms.py → Twilio REST API
     │      Email → INSERT INTO email_queue
     ▼
notification_attempts table  (all outcomes logged)
email_queue table            (pending emails)

(every 5-35 min cron)
     │
     ▼
channels/sendgrid_email.py
     │  reads email_queue WHERE status='queued' AND attempts < 3
     │  renders template with customer variables
     │  sends via SendGrid API
     │  updates email_queue.status = 'sent' / 'failed'
```

### Flow 4: Customer Replies

```
Customer texts back
     │
     │ POST /webhooks/twilio/inbound  (Twilio callback)
     ▼
routes/sms_inbound.py
     │  validates X-Twilio-Signature
     │  handles STOP words → inserts opt_outs
     │  inserts all other messages → inbound_messages table

(every 5 min cron)
     │
     ▼
workers/reply_processor.py
     │  reads unprocessed inbound_messages
     │  classifies: YES / NO / STOP / other
     │  for YES: marks appointment confirmed, sends staff Discord alert
     │  for NO: marks cancelled, sends urgent staff alert
     │  for STOP: ensure opt_out recorded
     │  updates inbound_messages.processed = TRUE
```

### Flow 5: Staff Dashboard

```
Browser → POST /dashboard/login
     │  validates password (global or per-staff bcrypt)
     │  creates signed session cookie
     ▼
routes/dashboard.py  (all protected by require_auth dependency)
     │
     ├── GET  /                       → overview stats
     ├── GET  /appointments           → appointment list
     ├── GET  /deliveries             → notification log
     ├── GET  /quarantine             → bad data queue
     ├── GET  /replies                → inbound SMS
     ├── GET  /settings               → system settings (admin)
     ├── GET  /templates              → message templates (admin)
     └── GET  /staff                  → staff management (admin)
```

---

## Database Schema Summary

| Table | Purpose |
|-------|---------|
| `appointments` | Canonical appointment records |
| `appointment_quarantine` | Records with data issues, pending manual review |
| `notification_attempts` | Every notification send attempt (source of truth for delivery log) |
| `email_queue` | Pending outbound emails (drained by sendgrid_email.py cron) |
| `inbound_messages` | All inbound SMS received from customers |
| `opt_outs` | Customers who have opted out (phone + channel) |
| `webhook_events` | Processed ClickUp webhook event IDs (idempotency) |
| `notification_templates` | Editable message templates per rule/language |
| `system_settings` | Key-value config (thresholds, alert numbers, kill switch) |
| `admin_users` | Staff logins with bcrypt passwords and roles |

---

## External Integrations

| Integration | Direction | Auth Method | Purpose |
|------------|-----------|-------------|---------|
| Google Calendar API | Inbound pull | Service account JSON | Sync appointment events |
| ClickUp Webhook | Inbound push | HMAC-SHA256 | HVAC task creation/update |
| Twilio SMS | Outbound send | Account SID + Auth Token | Send customer SMS reminders |
| Twilio Inbound SMS | Inbound push | X-Twilio-Signature | Receive customer replies |
| Twilio Status Callback | Inbound push | X-Twilio-Signature | Delivery receipts |
| SendGrid Email | Outbound send | API Key | Send customer email reminders |
| SendGrid Status Webhook | Inbound push | (not validated) | Email delivery events |
| Discord Webhook | Outbound push | Webhook URL | Staff reply alerts |

---

## Deployment Architecture (Current)

```
Windows Dev Machine
├── Python process: uvicorn webhook_server:app (port 8096)
├── ngrok tunnel → public HTTPS URL → port 8096
├── Windows Task Scheduler / cron:
│   ├── */15 min: python calendar_sync.py
│   ├── */30 min: python notification_engine.py
│   ├── */5  min: python workers/reply_processor.py
│   └── 5,35 min: python channels/sendgrid_email.py
└── storage.sqlite (single file DB, same directory as app)
```
