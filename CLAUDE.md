# Notification Service

**CURRENT_PHASE**: 6 — Deployed ✓

## Tech Stack
- **Backend**: Python 3.11+, FastAPI (Webhook server), SQLite3 (Local file `data.db`)
- **Frontend**: Jinja2 Templates (Bootstrap 5, Inter font)

## Rules
See MASTER CONTEXT in CLAUDE_CODE_PROMPTS_v1.md.

## Commands
```bash
python db/migrate.py                      # init schema
python calendar_sync.py                   # sync calendars
python calendar_sync.py --dry-run         # preview without writing
python notification_engine.py             # run engine manually
python workers/reply_processor.py         # process inbound replies
uvicorn webhook_server:app --host 127.0.0.1 --port 8096  # start server
pytest tests/ -v                          # run all tests
```

## Cron
```
*/15 * * * *  python calendar_sync.py
*/30 * * * *  python notification_engine.py
*/5  * * * *  python workers/reply_processor.py
5,35 * * * *  python channels/sendgrid_email.py
```

## Current Phase
CURRENT_PHASE: 6 — Deployed ✓
