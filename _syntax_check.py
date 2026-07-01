"""Temporary syntax check script — run once, then delete."""
import py_compile, sys

files = [
    "utils/sms_keywords.py",
    "routes/sms_inbound.py",
    "workers/reply_processor.py",
    "routes/sendgrid_status.py",
    "auth/session.py",
    "routes/dashboard.py",
    "db/migrate.py",
    "config.py",
    "webhook_server.py",
    "channels/twilio_sms.py",
    "channels/sendgrid_email.py",
]

ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK  {f}")
    except py_compile.PyCompileError as e:
        print(f"ERR {f}: {e}")
        ok = False

sys.exit(0 if ok else 1)
