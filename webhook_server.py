"""webhook_server.py — FastAPI app for Twilio webhooks.

Rule 10: Binds to 127.0.0.1:8096. Never use port 8080.
Rule 3:  All webhook routes validate X-Twilio-Signature (in route modules).

Run:  python webhook_server.py
  or: uvicorn webhook_server:app --host 127.0.0.1 --port 8096
"""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from db.migrate import run_migration
from routes.sms_inbound import router as sms_router
from routes.status_callback import router as status_router
from routes.sendgrid_status import router as sendgrid_router
from routes.clickup_webhook import router as clickup_router
from routes.dashboard import router as dashboard_router

app = FastAPI(title="Notification Service", docs_url=None, redoc_url=None)

@app.on_event("startup")
def on_startup():
    run_migration()

app.include_router(sms_router)
app.include_router(status_router)
app.include_router(sendgrid_router)
app.include_router(clickup_router)
app.include_router(dashboard_router)

from fastapi.staticfiles import StaticFiles
import os

if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index_redirect():
    """Redirect root to the admin dashboard."""
    return RedirectResponse(url="/dashboard/")


@app.get("/health")
def health():
    """Lightweight health-check endpoint."""
    return {"status": "ok"}


import config
import logging
logger = logging.getLogger(__name__)

def validate_config():
    """Check that all required credentials are present."""
    missing = []
    
    if not config.TWILIO_ACCOUNT_SID:
        missing.append("TWILIO_ACCOUNT_SID")
    if not config.TWILIO_AUTH_TOKEN:
        missing.append("TWILIO_AUTH_TOKEN")
    if not config.TWILIO_SMS_NUMBER:
        missing.append("TWILIO_SMS_NUMBER")
    if not config.SENDGRID_API_KEY:
        missing.append("SENDGRID_API_KEY")
    if not config.SENDGRID_FROM_EMAIL:
        missing.append("SENDGRID_FROM_EMAIL")
    if not config.CLICKUP_API_TOKEN:
        missing.append("CLICKUP_API_TOKEN")
    if not config.CLICKUP_WEBHOOK_SECRET:
        missing.append("CLICKUP_WEBHOOK_SECRET")
    
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Please check your .env file."
        )
    
    logger.info("All required credentials are present.")

validate_config()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webhook_server:app", host="127.0.0.1", port=8096, reload=False)
