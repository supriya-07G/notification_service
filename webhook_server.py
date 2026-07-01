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
from routes.sso import router as sso_router

app = FastAPI(title="Notification Service", docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request, call_next):
    """Add baseline security headers to every response.

    CSP is limited to frame-ancestors (clickjacking) rather than a full resource
    policy, to avoid breaking the dashboard's CDN assets. HSTS is safe behind the
    Cloudflare/TLS ingress.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response


@app.middleware("http")
async def enforce_password_reset(request, call_next):
    """Softly redirect users to the change-password page until it is completed."""
    from routes.dashboard import _should_redirect_to_change_password, get_current_user

    path = request.url.path
    user = get_current_user(request)
    if _should_redirect_to_change_password(path, user):
        return RedirectResponse(url="/dashboard/change-password?required=true", status_code=302)
    return await call_next(request)


@app.on_event("startup")
def on_startup():
    run_migration()

app.include_router(sms_router)
app.include_router(status_router)
app.include_router(sendgrid_router)
app.include_router(clickup_router)
app.include_router(dashboard_router)
app.include_router(sso_router)

from fastapi.staticfiles import StaticFiles
import os

if not os.path.exists("static"):
    os.makedirs("static")
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


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
    
    date_fields = [
        "CLICKUP_FIELD_DATE_HVAC", "CLICKUP_FIELD_DATE_INSULATION",
        "CLICKUP_FIELD_DATE_ELECTRICAL", "CLICKUP_FIELD_DATE_ASSESSMENT",
        "CLICKUP_FIELD_DATE_REMEDIATION", "CLICKUP_FIELD_DATE_SOLAR",
        "CLICKUP_FIELD_DATE_ROOF",
    ]
    for attr in date_fields:
        if not getattr(config, attr, None):
            missing.append(attr)

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
