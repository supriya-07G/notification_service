"""Central configuration loaded from .env via python-dotenv.

Rule 2: All credentials come from .env. Never hardcode API keys, tokens,
account SIDs, or passwords anywhere.
"""
import json
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()

# ── Database ────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "./storage.sqlite")

# ── Webhook / Server ────────────────────────────────────────────────────────
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://hooks.yourdomain.com")

# ── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Timezone ────────────────────────────────────────────────────────────────
TZ = os.getenv("TZ", "America/New_York")

# ── Google Calendar ─────────────────────────────────────────────────────────
GOOGLE_CREDENTIALS_PATH = os.getenv(
    "GOOGLE_CREDENTIALS_PATH", "./credentials/google_service_account.json"
)
_raw_calendar_ids = os.getenv("GOOGLE_CALENDAR_IDS", "")
GOOGLE_CALENDAR_IDS: list[str] = [
    cid.strip() for cid in _raw_calendar_ids.split(",") if cid.strip()
]

# ── Twilio ──────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_SMS_NUMBER = os.getenv("TWILIO_SMS_NUMBER", "")

# ── SendGrid ───────────────────────────────────────────────────────────────
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL", "")
SENDGRID_FROM_NAME = os.getenv("SENDGRID_FROM_NAME", "")
SENDGRID_WEBHOOK_VERIFY_KEY = os.getenv("SENDGRID_WEBHOOK_VERIFY_KEY", "")

# ── Discord ────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── ClickUp ────────────────────────────────────────────────────────────────
CLICKUP_WEBHOOK_SECRET = os.getenv("CLICKUP_WEBHOOK_SECRET", "")
CLICKUP_API_TOKEN = os.getenv("CLICKUP_API_TOKEN", "")
CLICKUP_LIST_ID = os.getenv("CLICKUP_LIST_ID", "901317175958")

# Custom field IDs for the ACTIVE HVAC list (901317175958).
# Set these in .env — run `python get_fields.py` to discover IDs.

# Core customer fields
CLICKUP_FIELD_NAME    = os.getenv(
    "CLICKUP_FIELD_NAME", "1ca299a3-2a6a-4102-b884-83d0ecaaa9c3"
)  # ⭐ Full Name
CLICKUP_FIELD_PHONE   = os.getenv(
    "CLICKUP_FIELD_PHONE", "dd2505b0-b8ef-43cb-a730-4193efe7b664"
)  # ⭐ Phone
CLICKUP_FIELD_EMAIL   = os.getenv(
    "CLICKUP_FIELD_EMAIL", "333855a2-a98b-4fe9-b951-a9fd075c46d1"
)  # ⭐ Email

# Service type (what the customer is getting — multi-select)
CLICKUP_FIELD_SCOPE   = os.getenv(
    "CLICKUP_FIELD_SCOPE", "50870f6d-1fa0-4cbc-af66-119a3de6d4b7"
)  # ⭐ Scope Of Work (Complete)

# Service-specific date fields (each service has its own date field)
CLICKUP_FIELD_DATE_HVAC        = os.getenv(
    "CLICKUP_FIELD_DATE_HVAC", "8454f592-135c-4fed-b40e-7711da85a641"
)  # ❄️ Installation Start Date
CLICKUP_FIELD_DATE_INSULATION  = os.getenv(
    "CLICKUP_FIELD_DATE_INSULATION", "7cec17d3-97b2-4c8f-814c-bc0e94b9ba3d"
)  # ❄️ Insulation Date
CLICKUP_FIELD_DATE_ELECTRICAL  = os.getenv(
    "CLICKUP_FIELD_DATE_ELECTRICAL", "9cfaccdd-707a-4eeb-9a0a-9806066dbb20"
)  # ⭐ Electrical Date
CLICKUP_FIELD_DATE_ASSESSMENT  = os.getenv(
    "CLICKUP_FIELD_DATE_ASSESSMENT", "86b8c3a8-5405-4dbf-a74f-47ff510904c0"
)  # ❄️ Assessment Date
CLICKUP_FIELD_DATE_REMEDIATION = os.getenv(
    "CLICKUP_FIELD_DATE_REMEDIATION", "61f3ff62-d911-4a23-9779-640d331f79ce"
)  # ⭐ Remediation Date
CLICKUP_FIELD_DATE_SOLAR       = os.getenv(
    "CLICKUP_FIELD_DATE_SOLAR", "e55a9e51-7d2d-4107-8768-7a38ae37935e"
)  # ⭐ Install Date (Solar)
CLICKUP_FIELD_DATE_ROOF        = os.getenv(
    "CLICKUP_FIELD_DATE_ROOF", "8bc34e30-389e-4c49-b3d9-4cdca522f8a2"
)  # ✅ Site Visit Date (Roof)

# Payment method — internal use only, not shown to customers
CLICKUP_FIELD_PROGRAM = os.getenv(
    "CLICKUP_FIELD_PROGRAM", "8fcd3ac4-8323-4f09-8826-c5d8e96fbd46"
)  # ❄️ HVAC Program

# ── Dashboard (DEPRECATED — kept for backward compatibility) ───────────────

# ── Google SSO ────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# ── Session Auth (Phase 4) ─────────────────────────────────────────────────
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")
SESSION_SECURE_COOKIE = os.getenv("SESSION_SECURE_COOKIE", "true")
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", "28800"))

# ── Calendar Source Map ────────────────────────────────────────────────────

# Manual mapping for clarity (overrides the default order)
EXPLICIT_SOURCE_MAP = {
    "c_bb34a69d47bae1fc042544bbb2810e6ee4b62e19edac3dd82276a0aa9632dd7a@group.calendar.google.com": "ELETRICIAN SCHEDULE",
    "c_a468925a6cde2b69bf603f4a4be6816a7f2d6bb7f478d053c01e5661d2651591@group.calendar.google.com": "FINAL INSPECTION",
    "c_1568534dd55e30812aeb9b2678258b456fdd6ae23f29a509d436fa75294ee9cb@group.calendar.google.com": "HVAC INSPECTION",
    "c_22814da0030e4ab0cc3a2676ca39f73a0d65d804252c1706152c5b1d23cafc16@group.calendar.google.com": "HVAC INSTALLATION",
    "c_da85522be5577366806669905049a47f4dbaec84e0d357e2a58eff5c1e62bc52@group.calendar.google.com": "INSULATION SCHEDULE",
    "c_88a46f68833fe4bf799281e123cdfc4eebae9cfa6cd4fe29bc9999792e0918a4@group.calendar.google.com": "OIL TANK REMOVAL",
    "c_9435362249db8646dde779e10cde4142a6abdb26b2945b7d6abc82ed9551b794@group.calendar.google.com": "ROOF WORK",
    "c_classroom5ae32f53@group.calendar.google.com": "SITE SURVEY AND MASS SAVE AUDIT",
    "c_24f73f375e44718d99f28acbee724ad4d133b3299b182400428fd13ed2a0ebf6@group.calendar.google.com": "SOLAR INSTALL",
    "c_bc50a51835578930db45dcf09a4e1dfbd7f16abf2ec85bd4443f3281ea582f50@group.calendar.google.com": "TREE WORK",
    "trevor@ecosave-group.com": "Trevor Miller",
}

# Maps each Google Calendar ID to a source name.
# First = 'hvac', second = 'solar', third = 'inspections', rest = 'other'.
# Override with CALENDAR_SOURCE_MAP_JSON env var (JSON string).
_default_source_names = ["hvac", "solar", "inspections"]
_json_override = os.getenv("CALENDAR_SOURCE_MAP_JSON", "")

if _json_override:
    CALENDAR_SOURCE_MAP: dict[str, str] = json.loads(_json_override)
else:
    CALENDAR_SOURCE_MAP: dict[str, str] = EXPLICIT_SOURCE_MAP.copy()
    for idx, cal_id in enumerate(GOOGLE_CALENDAR_IDS):
        if cal_id not in CALENDAR_SOURCE_MAP:
            if idx < len(_default_source_names):
                CALENDAR_SOURCE_MAP[cal_id] = _default_source_names[idx]
            else:
                CALENDAR_SOURCE_MAP[cal_id] = "other"

# ── Notification Rules ─────────────────────────────────────────────────────
NOTIFICATION_RULES: list[dict] = [
    {
        "name": "customer_72h",
        "hours_before": 72,
        "channels": ["sms", "email"],
        "audience": "customer",
    },
    {
        "name": "customer_24h",
        "hours_before": 24,
        "channels": ["sms", "email"],
        "audience": "customer",
    },
    {
        "name": "customer_2h",
        "hours_before": 2,
        "channels": ["sms"],
        "audience": "customer",
    },
]
