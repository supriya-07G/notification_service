import datetime
import json
import os
import re
from fastapi import APIRouter, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
import logging
import uuid
import time
import pytz
from urllib.parse import urlencode

import config
import notification_engine
from db.init import get_connection
from db.settings import Settings
from db.admin_users import authenticate, is_allowed_email, hash_password, validate_password_strength
from auth.session import (
    get_current_user,
    create_session_cookie,
    clear_session_cookie,
    require_login,
    is_rate_limited,
    record_failed_attempt,
    clear_failed_attempts,
)
from auth.csrf import generate_csrf_token, validate_csrf_token
import phonenumbers

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates/dashboard")
router = APIRouter(prefix="/dashboard")


# ── Private helper logic ──────────────────────────────────────────────────
_APPT_TYPE_KEYWORDS = {
    "estimate": "estimate",
    "install": "install",
    "service": "service",
    "repair": "service",
    "inspection": "inspection",
}

def detect_appointment_type(title: str) -> str:
    lower_title = title.lower() if title else ""
    for keyword, appt_type in _APPT_TYPE_KEYWORDS.items():
        if keyword in lower_title:
            return appt_type
    return "service"

_LANG_TAG_RE = re.compile(r"\[LANG:(EN|PT|ES)\]", re.IGNORECASE)

def extract_language(title: str, description: str) -> tuple[str, str]:
    combined = f"{title or ''} {description or ''}"
    m = _LANG_TAG_RE.search(combined)
    if m:
        return m.group(1).lower(), "tag"
    return "en", "default"

_NO_REMINDER_RE = re.compile(r"\[NO\s*REMINDER\]", re.IGNORECASE)

def has_no_reminder(title: str, description: str) -> bool:
    combined = f"{title or ''} {description or ''}"
    return bool(_NO_REMINDER_RE.search(combined))

def get_nav_context(conn):
    quarantine_count = conn.execute(
        "SELECT COUNT(*) FROM appointment_quarantine WHERE resolved = FALSE OR resolved = 0"
    ).fetchone()[0]
    unprocessed_replies_count = conn.execute(
        "SELECT COUNT(*) FROM inbound_messages WHERE processed = FALSE OR processed = 0"
    ).fetchone()[0]
    return {
        "quarantine_count": quarantine_count,
        "unprocessed_replies_count": unprocessed_replies_count
    }

def _get_client_ip(request: Request) -> str:
    """Extract client IP for logging."""
    import os
    direct_ip = request.client.host if request.client else "unknown"
    trusted_proxies = os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1").split(",")
    trusted_proxies = [ip.strip() for ip in trusted_proxies if ip.strip()]

    if direct_ip not in trusted_proxies:
        return direct_ip

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
        
    return direct_ip


# ── LOGO ROUTES ─────────────────────────────────────────────────────────────
@router.get("/logo_partner.png")
def get_logo_partner():
    path = "static/logo_partner.png"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Partner Logo not found")
    return FileResponse(path)

@router.get("/logo_clean.png")
def get_logo_clean():
    path = "static/logo_clean.png"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Clean Logo not found")
    return FileResponse(path)


# ── LOGIN / LOGOUT ROUTES ──────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Show the login page. Redirects to dashboard if already logged in."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard/", status_code=302)

    rate_limited = is_rate_limited(request)
    csrf_token = generate_csrf_token()

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "rate_limited": rate_limited,
            "csrf_token": csrf_token,
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    """Process login form submission."""
    ip = _get_client_ip(request)

    # 1. Validate CSRF
    if not validate_csrf_token(csrf_token):
        new_csrf = generate_csrf_token()
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid request. Please try again.",
                "rate_limited": False,
                "csrf_token": new_csrf,
            },
            status_code=400,
        )

    # 2. Check rate limit
    if is_rate_limited(request):
        new_csrf = generate_csrf_token()
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": None,
                "rate_limited": True,
                "csrf_token": new_csrf,
            },
            status_code=429,
        )

    # 3. Check domain (use generic error — don't reveal domain restriction)
    if not is_allowed_email(email):
        record_failed_attempt(request)
        logger.warning("Login failed: ip=%s reason=invalid_credentials", ip)
        new_csrf = generate_csrf_token()
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid email or password.",
                "rate_limited": False,
                "csrf_token": new_csrf,
            },
        )

    # 4. Authenticate
    user = authenticate(email, password)
    if user is None:
        record_failed_attempt(request)
        logger.warning("Login failed: ip=%s reason=invalid_credentials", ip)
        new_csrf = generate_csrf_token()
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid email or password.",
                "rate_limited": False,
                "csrf_token": new_csrf,
            },
        )

    # 5. Success — create session
    clear_failed_attempts(request)
    logger.info("Login success: user=%s role=%s ip=%s", user["email"], user["role"], ip)

    response = RedirectResponse(url="/dashboard/", status_code=302)
    create_session_cookie(response, user["email"], role=user["role"])
    return response


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    user = get_current_user(request)
    ip = _get_client_ip(request)
    if user:
        logger.info("Logout: user=%s ip=%s", user["email"], ip)

    response = RedirectResponse(url="/dashboard/login", status_code=302)
    clear_session_cookie(response)
    return response


# ── GET ROUTES ──────────────────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    conn = get_connection()
    try:
        # Today's stats
        stats_rows = conn.execute("""
            SELECT status, COUNT(*) as cnt
            FROM notification_attempts
            WHERE date(sent_at, 'localtime') = date('now', 'localtime')
            GROUP BY status
        """).fetchall()
        
        stats = {"sent": 0, "delivered": 0, "failed": 0, "pending": 0}
        total_sent = 0
        for row in stats_rows:
            status = row["status"]
            cnt = row["cnt"]
            total_sent += cnt
            if status == "delivered":
                stats["delivered"] += cnt
            elif status in ["failed", "undelivered", "bounced"]:
                stats["failed"] += cnt
            elif status in ["pending", "queued"]:
                stats["pending"] += cnt
            else:
                stats["pending"] += cnt
        stats["sent"] = total_sent
        
        settings = Settings(conn)
        is_paused = settings.is_paused()
        is_quiet_hours_active = settings.is_quiet_hours_active()
        
        failures = conn.execute("""
            SELECT n.*, a.customer_name
            FROM notification_attempts n
            LEFT JOIN appointments a ON n.appointment_id = a.id
            WHERE n.status IN ('failed', 'undelivered', 'bounced')
            ORDER BY n.sent_at DESC
            LIMIT 5
        """).fetchall()
        
        nav = get_nav_context(conn)
        
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "active_page": "overview",
                "stats": stats,
                "is_paused": is_paused,
                "is_quiet_hours_active": is_quiet_hours_active,
                "recent_failures": failures,
                "user": user,
                **nav
            }
        )
    finally:
        conn.close()

@router.get("/appointments", response_class=HTMLResponse)
async def appointments(
    request: Request,
    search: str = "",
    date: str = "",
    source: str = "",
    status: str = "",
    start_date: str = "",
    end_date: str = "",
):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    conn = get_connection()
    try:
        # Default: show appointments from yesterday through 30 days ahead
        query = """
            SELECT * FROM appointments
            WHERE appointment_at >= datetime('now', '-1 day')
              AND appointment_at <= datetime('now', '+30 days')
        """
        params = []

        # Override with explicit date range if provided
        if start_date:
            query = "SELECT * FROM appointments WHERE appointment_at >= ?"
            params = [start_date + " 00:00:00"]
            if end_date:
                query += " AND appointment_at <= ?"
                params.append(end_date + " 23:59:59")
        elif end_date:
            query = "SELECT * FROM appointments WHERE appointment_at <= ?"
            params = [end_date + " 23:59:59"]

        if search:
            query += " AND (customer_name LIKE ? OR customer_phone LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])

        if date:
            query += " AND date(appointment_at, 'localtime') = ?"
            params.append(date)

        if source:
            query += " AND calendar_source = ?"
            params.append(source)

        if status:
            query += """
                AND (
                    SELECT n.status FROM notification_attempts n
                    WHERE n.appointment_id = appointments.id
                    ORDER BY n.sent_at DESC LIMIT 1
                ) = ?
            """
            params.append(status)

        query += " ORDER BY appointment_at ASC"

        appts = conn.execute(query, params).fetchall()

        sources_rows = conn.execute("SELECT DISTINCT calendar_source FROM appointments").fetchall()
        available_sources = [r["calendar_source"] for r in sources_rows if r["calendar_source"]]

        dates_rows = conn.execute("""
            SELECT DISTINCT date(appointment_at, 'localtime') as appt_date
            FROM appointments
            WHERE appointment_at >= datetime('now', '-1 day')
              AND appointment_at <= datetime('now', '+30 days')
            ORDER BY appt_date ASC
        """).fetchall()
        available_dates = [r["appt_date"] for r in dates_rows if r["appt_date"]]
        
        appt_ids = [appt["id"] for appt in appts]
        attempts_by_appt = {}
        if appt_ids:
            placeholders = ",".join("?" for _ in appt_ids)
            attempts_rows = conn.execute(
                f"""
                SELECT appointment_id, rule_name, channel, status
                FROM notification_attempts
                WHERE appointment_id IN ({placeholders})
                ORDER BY sent_at ASC
                """,
                appt_ids
            ).fetchall()
            for r in attempts_rows:
                aid = r["appointment_id"]
                if aid not in attempts_by_appt:
                    attempts_by_appt[aid] = []
                attempts_by_appt[aid].append(r)
                
        nav = get_nav_context(conn)
        
        return templates.TemplateResponse(
            "appointments.html",
            {
                "request": request,
                "active_page": "appointments",
                "appointments": appts,
                "attempts_by_appt": attempts_by_appt,
                "available_sources": available_sources,
                "all_calendar_sources": list(set(config.CALENDAR_SOURCE_MAP.values())),
                "available_dates": available_dates,
                "current_filters": {
                    "search": search,
                    "date": date,
                    "source": source,
                    "status": status,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "user": user,
                "csrf_token": generate_csrf_token(),
                **nav
            }
        )
    finally:
        conn.close()

@router.post("/appointments/add")
async def add_appointment(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(...),
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    customer_email: str = Form(""),
    calendar_source: str = Form("manual"),
    appointment_type: str = Form(...),
    appointment_at: str = Form(...),
    location: str = Form(""),
    notes: str = Form("")
):
    redirect = require_login(request)
    if redirect:
        return redirect
        
    if not validate_csrf_token(csrf_token):
        query = urlencode({"error": "Invalid CSRF token. Please try again."})
        return RedirectResponse(url=f"/dashboard/appointments?{query}", status_code=303)
        
    try:
        # Try to parse and validate phone, but fall back to raw input if it fails
        parsed_phone = phonenumbers.parse(customer_phone, "US")
        if phonenumbers.is_valid_number(parsed_phone):
            formatted_phone = phonenumbers.format_number(parsed_phone, phonenumbers.PhoneNumberFormat.E164)
        else:
            formatted_phone = customer_phone.strip()
    except Exception:
        # If parsing completely fails, just use the raw input and let Twilio handle it
        formatted_phone = customer_phone.strip()
        
    try:
        # Validate and convert datetime to UTC
        # appointment_at from datetime-local input is typically 'YYYY-MM-DDTHH:MM'
        local_dt = datetime.datetime.fromisoformat(appointment_at)
        tz = pytz.timezone(config.TZ)
        local_dt = tz.localize(local_dt)
        utc_dt = local_dt.astimezone(pytz.utc)
        utc_str = utc_dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error("Failed to parse appointment datetime: %s", e)
        query = urlencode({"error": "Invalid appointment date & time."})
        return RedirectResponse(url=f"/dashboard/appointments?{query}", status_code=303)
        
    appt_id = f"manual_{uuid.uuid4().hex[:8]}_{int(time.time())}"
    
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO appointments (
                id, calendar_source, customer_name, customer_phone, customer_email,
                appointment_at, appointment_type, location, notes, language, language_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appt_id, calendar_source, customer_name.strip(), formatted_phone, 
                customer_email.strip() if customer_email else None,
                utc_str, appointment_type, location.strip() if location else None, 
                notes.strip() if notes else None, "en", "manual"
            )
        )
        conn.commit()
        logger.info("Manually added appointment %s for %s", appt_id, formatted_phone)
        
        # Auto-trigger the notification engine in the background
        background_tasks.add_task(notification_engine.run)
        
        query = urlencode({"success": f"Appointment for {customer_name} successfully added!"})
        return RedirectResponse(url=f"/dashboard/appointments?{query}", status_code=303)
    except Exception as e:
        logger.error("Database error while adding manual appointment: %s", e)
        query = urlencode({"error": "A database error occurred. Please try again."})
        return RedirectResponse(url=f"/dashboard/appointments?{query}", status_code=303)
    finally:
        conn.close()

@router.get("/quarantine", response_class=HTMLResponse)
async def quarantine_page(
    request: Request,
    search: str = "",
    start_date: str = "",
    end_date: str = "",
    show_past: str = "",
    error_quarantine_id: int = None,
    error_message: str = None,
    phone: str = "",
    customer_name: str = "",
):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    conn = get_connection()
    try:
        query = "SELECT * FROM appointment_quarantine WHERE (resolved = FALSE OR resolved = 0)"
        params = []
        
        if not show_past:
            query += " AND (appointment_at >= datetime('now', '-1 day') OR appointment_at IS NULL)"
            
        if search:
            query += " AND (raw_title LIKE ? OR quarantine_reason LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
            
        if start_date:
            query += " AND date(detected_at) >= ?"
            params.append(start_date)
            
        if end_date:
            query += " AND date(detected_at) <= ?"
            params.append(end_date)
            
        query += """ ORDER BY 
            CASE 
                WHEN appointment_at IS NOT NULL AND appointment_at >= datetime('now', '-1 day') THEN 1
                WHEN appointment_at IS NULL THEN 2
                ELSE 3
            END,
            appointment_at ASC, detected_at DESC
        """
        
        items = conn.execute(query, params).fetchall()
        
        nav = get_nav_context(conn)
        csrf_token = generate_csrf_token()

        return templates.TemplateResponse(
            "quarantine.html",
            {
                "request": request,
                "active_page": "quarantine",
                "quarantine_items": items,
                "error_quarantine_id": error_quarantine_id,
                "error_message": error_message,
                "form_values": {
                    "phone": phone,
                    "customer_name": customer_name,
                    "search": search,
                    "start_date": start_date,
                    "end_date": end_date,
                    "show_past": show_past
                },
                "csrf_token": csrf_token,
                "user": user,
                **nav
            }
        )
    finally:
        conn.close()

@router.get("/deliveries", response_class=HTMLResponse)
async def deliveries(request: Request, status: str = "", channel: str = "", start_date: str = "", end_date: str = "", page: int = 1):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    conn = get_connection()
    try:
        limit = 50
        offset = (page - 1) * limit
        
        query = """
            SELECT n.*, a.customer_name
            FROM notification_attempts n
            LEFT JOIN appointments a ON n.appointment_id = a.id
            WHERE 1=1
        """
        count_query = "SELECT COUNT(*) FROM notification_attempts n WHERE 1=1"
        params = []
        
        if status:
            if status == "pending":
                query += " AND n.status IN ('pending', 'queued')"
                count_query += " AND n.status IN ('pending', 'queued')"
            elif status == "failed":
                query += " AND n.status IN ('failed', 'undelivered', 'bounced')"
                count_query += " AND n.status IN ('failed', 'undelivered', 'bounced')"
            else:
                query += " AND n.status = ?"
                count_query += " AND n.status = ?"
                params.append(status)

        if channel:
            query += " AND n.channel = ?"
            count_query += " AND n.channel = ?"
            params.append(channel)

        if start_date:
            query += " AND n.sent_at >= ?"
            count_query += " AND n.sent_at >= ?"
            params.append(start_date + " 00:00:00")

        if end_date:
            query += " AND n.sent_at <= ?"
            count_query += " AND n.sent_at <= ?"
            params.append(end_date + " 23:59:59")

        query += " ORDER BY n.sent_at DESC LIMIT ? OFFSET ?"
        
        attempts = conn.execute(query, params + [limit, offset]).fetchall()
        total_records = conn.execute(count_query, params).fetchone()[0]
        
        import math
        total_pages = max(1, math.ceil(total_records / limit))
        
        nav = get_nav_context(conn)
        
        return templates.TemplateResponse(
            "deliveries.html",
            {
                "request": request,
                "active_page": "deliveries",
                "attempts": attempts,
                "page": page,
                "total_pages": total_pages,
                "current_status": status,
                "current_channel": channel,
                "current_start_date": start_date,
                "current_end_date": end_date,
                "user": user,
                **nav
            }
        )
    finally:
        conn.close()

@router.get("/deliveries/export")
async def export_deliveries_csv(
    request: Request,
    status: str = "",
    channel: str = "",
    start_date: str = "",
    end_date: str = "",
):
    """Export delivery logs as CSV. Admin only."""
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    # Admin-only check
    if not user or user.get("role") != "admin":
        logger.warning("Non-admin user %s attempted CSV export", user.get("email") if user else "unknown")
        return RedirectResponse(url="/dashboard/deliveries", status_code=302)

    import csv
    import io
    import json

    conn = get_connection()
    try:
        # Build filtered query
        query = """
            SELECT
                a.customer_name,
                a.customer_phone,
                a.customer_email,
                n.channel,
                n.rule_name,
                n.to_address,
                n.status,
                n.sent_at,
                n.status_updated_at,
                n.error_code,
                n.error_message,
                n.provider_sid
            FROM notification_attempts n
            LEFT JOIN appointments a ON n.appointment_id = a.id
            WHERE 1=1
        """
        params = []

        if status:
            if status == "pending":
                query += " AND n.status IN ('pending', 'queued')"
            elif status == "failed":
                query += " AND n.status IN ('failed', 'undelivered', 'bounced')"
            else:
                query += " AND n.status = ?"
                params.append(status)

        if channel:
            query += " AND n.channel = ?"
            params.append(channel)

        if start_date:
            query += " AND n.sent_at >= ?"
            params.append(start_date + " 00:00:00")

        if end_date:
            query += " AND n.sent_at <= ?"
            params.append(end_date + " 23:59:59")

        query += " ORDER BY n.sent_at DESC LIMIT 10000"

        # Audit log BEFORE generating CSV (PII tracking)
        filters_used = {
            "status": status or "all",
            "channel": channel or "all",
            "start_date": start_date or "none",
            "end_date": end_date or "none",
        }
        conn.execute(
            """INSERT INTO audit_log (action, source, entity_id, details)
               VALUES ('csv_export', 'dashboard', ?, ?)""",
            (user["email"], json.dumps(filters_used)),
        )
        conn.commit()

        rows = conn.execute(query, params).fetchall()

        # Generate CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Customer Name", "Customer Phone", "Customer Email",
            "Channel", "Rule", "To Address", "Status",
            "Sent At", "Updated At", "Error Code", "Error Message",
            "Provider SID"
        ])
        for row in rows:
            writer.writerow([
                row["customer_name"] or "",
                row["customer_phone"] or "",
                row["customer_email"] or "",
                row["channel"] or "",
                row["rule_name"] or "",
                row["to_address"] or "",
                row["status"] or "",
                row["sent_at"] or "",
                row["status_updated_at"] or "",
                row["error_code"] or "",
                row["error_message"] or "",
                row["provider_sid"] or "",
            ])

        csv_content = output.getvalue()
        logger.info("CSV export by %s: %d rows, filters=%s", user["email"], len(rows), filters_used)

        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=delivery_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            },
        )
    finally:
        conn.close()

@router.get("/replies", response_class=HTMLResponse)
async def replies(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    conn = get_connection()
    try:
        items = conn.execute("SELECT * FROM inbound_messages ORDER BY received_at DESC").fetchall()
        nav = get_nav_context(conn)
        return templates.TemplateResponse(
            "replies.html",
            {
                "request": request,
                "active_page": "replies",
                "replies": items,
                "user": user,
                **nav
            }
        )
    finally:
        conn.close()

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: bool = False, password_success: bool = False, password_errors: str = ""):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    conn = get_connection()
    try:
        settings_rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
        settings_dict = {row["key"]: row["value"] for row in settings_rows}
        nav = get_nav_context(conn)
        csrf_token = generate_csrf_token()
        
        errors_list = password_errors.split("|") if password_errors else []
        
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "active_page": "settings",
                "settings": settings_dict,
                "saved": saved,
                "password_success": password_success,
                "password_errors": errors_list,
                "csrf_token": csrf_token,
                "user": user,
                **nav
            }
        )
    finally:
        conn.close()

@router.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, saved: bool = False, error: str = ""):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    conn = get_connection()
    try:
        items = conn.execute("""
            SELECT * FROM message_templates
            ORDER BY channel, rule_name, language, appointment_type
        """).fetchall()
        nav = get_nav_context(conn)
        return templates.TemplateResponse(
            "templates.html",
            {
                "request": request,
                "active_page": "templates",
                "templates": items,
                "saved": saved,
                "error": error,
                "csrf_token": generate_csrf_token(),
                "user": user,
                **nav
            }
        )
    finally:
        conn.close()

# ── POST ROUTES ─────────────────────────────────────────────────────────────
@router.post("/appointments/{id}/language")
async def set_language(request: Request, id: str, language: str = Form(...), csrf_token: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/appointments?error=Invalid+CSRF+token", status_code=303)

    conn = get_connection()
    try:
        conn.execute(
            """UPDATE appointments
               SET language = ?, language_source = 'override', updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            [language, id]
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/dashboard/appointments", status_code=303)

@router.post("/appointments/{id}/no-reminder")
async def toggle_no_reminder(request: Request, id: str, csrf_token: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/appointments?error=Invalid+CSRF+token", status_code=303)

    conn = None
    try:
        conn = get_connection()
        current = conn.execute(
            "SELECT no_reminder FROM appointments WHERE id = ?", [id]
        ).fetchone()
        if not current:
            return RedirectResponse(url="/dashboard/appointments?error=Appointment+not+found", status_code=303)
        new_val = 0 if current["no_reminder"] else 1
        conn.execute(
            "UPDATE appointments SET no_reminder = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [new_val, id]
        )
        conn.commit()
    except Exception as e:
        logger.error("Error toggling no_reminder for id=%s: %s", id, e)
        return RedirectResponse(url="/dashboard/appointments?error=Failed+to+update+reminder+status", status_code=303)
    finally:
        if conn:
            conn.close()
    referer = request.headers.get("referer", "/dashboard/appointments")
    return RedirectResponse(url=referer, status_code=303)


@router.post("/appointments/{id}/delete")
async def delete_appointment(request: Request, id: str, csrf_token: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/appointments?error=Invalid+CSRF+token", status_code=303)

    user = get_current_user(request)
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT customer_name, customer_phone, appointment_at FROM appointments WHERE id = ?",
            [id]
        ).fetchone()

        if not row:
            return RedirectResponse(url="/dashboard/appointments?error=Appointment+not+found", status_code=303)

        conn.execute("DELETE FROM appointments WHERE id = ?", [id])
        conn.execute("DELETE FROM notification_attempts WHERE appointment_id = ?", [id])
        try:
            conn.execute(
                """INSERT INTO audit_log (action, source, entity_id, details)
                   VALUES ('appointment_deleted', 'dashboard', ?, ?)""",
                [
                    id,
                    json.dumps({
                        "deleted_by": user.get("email") if user else "unknown",
                        "customer_name": row["customer_name"],
                        "customer_phone": row["customer_phone"],
                        "appointment_at": row["appointment_at"],
                    })
                ]
            )
        except Exception as audit_err:
            logger.warning("audit_log insert failed: %s", audit_err)
        conn.commit()
    except Exception as e:
        logger.error("Error deleting appointment id=%s: %s", id, e)
        return RedirectResponse(url="/dashboard/appointments?error=Failed+to+delete+appointment", status_code=303)
    finally:
        if conn:
            conn.close()

    referer = request.headers.get("referer", "/dashboard/appointments")
    return RedirectResponse(url=referer, status_code=303)

@router.post("/quarantine/{id}/resolve")
async def resolve_quarantine(
    request: Request,
    id: int,
    phone: str = Form(...),
    customer_name: str = Form(...),
    csrf_token: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/quarantine?error=Invalid+CSRF+token", status_code=303)
    user = get_current_user(request)

    conn = get_connection()
    try:
        try:
            parsed = phonenumbers.parse(phone, "US")
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError("Not a valid US number")
            formatted_phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except Exception:
            items = conn.execute("""
                SELECT * FROM appointment_quarantine
                WHERE resolved = FALSE OR resolved = 0
                ORDER BY detected_at DESC
            """).fetchall()
            nav = get_nav_context(conn)
            return templates.TemplateResponse(
                "quarantine.html",
                {
                    "request": request,
                    "active_page": "quarantine",
                    "quarantine_items": items,
                    "error_quarantine_id": id,
                    "error_message": f"Invalid phone number: '{phone}'. Please provide a valid US number (e.g., +1234567890 or 213-373-4253).",
                    "form_values": {
                        "phone": phone,
                        "customer_name": customer_name
                    },
                    "csrf_token": generate_csrf_token(),
                    "user": user,
                    **nav
                }
            )
        
        item = conn.execute("SELECT * FROM appointment_quarantine WHERE id = ?", [id]).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Quarantine item not found")
            
        appt_type = detect_appointment_type(item["raw_title"])
        lang, lang_src = extract_language(item["raw_title"], item["raw_description"])
        no_rem = has_no_reminder(item["raw_title"], item["raw_description"])
        
        conn.execute(
            """INSERT OR REPLACE INTO appointments (
                id, calendar_source, customer_name, customer_phone,
                appointment_at, appointment_type, language, language_source,
                no_reminder, raw_title, raw_description, synced_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            [
                item["gcal_event_id"],
                item["calendar_source"],
                customer_name,
                formatted_phone,
                item["appointment_at"],
                appt_type,
                lang,
                "override" if lang != "en" else lang_src,
                no_rem,
                item["raw_title"],
                item["raw_description"]
            ]
        )
        
        resolved_by = user["email"] if user else "unknown"
        conn.execute(
            """UPDATE appointment_quarantine
               SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP, resolved_by = ?
               WHERE id = ?""",
            [resolved_by, id]
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/dashboard/quarantine", status_code=303)

@router.post("/quarantine/{id}/dismiss")
async def dismiss_quarantine(request: Request, id: int, csrf_token: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/quarantine?error=Invalid+CSRF+token", status_code=303)
    user = get_current_user(request)

    conn = get_connection()
    try:
        resolved_by = user["email"] if user else "unknown"
        conn.execute(
            """UPDATE appointment_quarantine
               SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP, resolved_by = ?
               WHERE id = ?""",
            [resolved_by, id]
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/dashboard/quarantine", status_code=303)

@router.post("/settings")
async def update_settings(
    request: Request,
    quiet_hours_start: str = Form(...),
    quiet_hours_end: str = Form(...),
    timezone: str = Form(...),
    csrf_token: str = Form(""),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/settings?error=Invalid+CSRF+token", status_code=303)

    form_data = await request.form()
    user = get_current_user(request)
    updated_by = user["email"] if user else "system"
    
    conn = get_connection()
    try:
        settings = Settings(conn)
        
        keys = [
            "notifications_paused", "sms_enabled", "email_enabled", 
            "quiet_hours_enabled", "reminder_72h_enabled", 
            "reminder_24h_enabled", "reminder_2h_enabled"
        ]
        for key in keys:
            val = "true" if key in form_data else "false"
            settings.set(key, val, updated_by=updated_by)
            
        settings.set("quiet_hours_start", quiet_hours_start, updated_by=updated_by)
        settings.set("quiet_hours_end", quiet_hours_end, updated_by=updated_by)
        settings.set("timezone", timezone, updated_by=updated_by)
    finally:
        conn.close()
        
    return RedirectResponse(url="/dashboard/settings?saved=true", status_code=303)

@router.post("/settings/password")
async def update_global_password(
    request: Request,
    csrf_token: str = Form(""),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    from db.admin_users import validate_password_strength, hash_password
    import urllib.parse
    
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/settings?password_errors=Invalid+request.+Please+try+again.", status_code=303)
        
    if new_password != confirm_password:
        return RedirectResponse(url="/dashboard/settings?password_errors=Passwords+do+not+match.", status_code=303)
        
    errors = validate_password_strength(new_password)
    if errors:
        err_str = urllib.parse.quote("|".join(errors))
        return RedirectResponse(url=f"/dashboard/settings?password_errors={err_str}", status_code=303)
        
    hashed = hash_password(new_password)
    user = get_current_user(request)
    updated_by = user["email"] if user else "system"
    
    conn = get_connection()
    try:
        settings_obj = Settings(conn)
        settings_obj.set("dashboard_password_hash", hashed, updated_by=updated_by)
    finally:
        conn.close()
        
    return RedirectResponse(url="/dashboard/settings?password_success=true", status_code=303)

@router.post("/templates")
async def add_template(
    request: Request,
    channel: str = Form(...),
    appointment_type: str = Form(...),
    language: str = Form(...),
    rule_name: str = Form(...),
    body: str = Form(...),
    subject: str = Form(None),
    is_active: str = Form("false"),
    csrf_token: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/templates?error=Invalid+CSRF+token", status_code=303)

    conn = get_connection()
    try:
        active_val = 1 if is_active == "true" or is_active == "on" else 0
        conn.execute(
            """INSERT INTO message_templates
               (channel, appointment_type, language, rule_name, subject, body, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [channel, appointment_type, language, rule_name, subject or None, body, active_val]
        )
        conn.commit()
    except Exception as e:
        return RedirectResponse(url=f"/dashboard/templates?error={str(e)}", status_code=303)
    finally:
        conn.close()
    return RedirectResponse(url="/dashboard/templates?saved=true", status_code=303)

@router.post("/templates/{id}")
async def edit_template(
    request: Request,
    id: int,
    body: str = Form(...),
    subject: str = Form(None),
    is_active: str = Form("false"),
    csrf_token: str = Form(...),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/templates?error=Invalid+CSRF+token", status_code=303)

    conn = get_connection()
    try:
        active_val = 1 if is_active == "true" or is_active == "on" else 0
        conn.execute(
            """UPDATE message_templates
               SET body = ?, subject = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            [body, subject or None, active_val, id]
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/dashboard/templates?saved=true", status_code=303)

@router.post("/templates/{id}/delete")
async def delete_template(request: Request, id: int, csrf_token: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/templates?error=Invalid+CSRF+token", status_code=303)

    import sqlite3
    from urllib.parse import urlencode
    
    user = get_current_user(request)
    user_email = user["email"] if user else "unknown"
    logger.info("User %s attempting to delete template %s", user_email, id)

    conn = get_connection()
    try:
        conn.execute("DELETE FROM message_templates WHERE id = ?", [id])
        conn.commit()
        logger.info("Successfully deleted template %s", id)
        return RedirectResponse(url="/dashboard/templates?saved=true", status_code=303)
    except sqlite3.IntegrityError as e:
        logger.error("Foreign key constraint failed when deleting template %s: %s", id, e)
        error_msg = urlencode({"error": "Cannot delete template. It is currently referenced by queued emails. You can deactivate it instead."})
        return RedirectResponse(url=f"/dashboard/templates?{error_msg}", status_code=303)
    except Exception as e:
        logger.error("Error deleting template %s: %s", id, e)
        error_msg = urlencode({"error": "An unexpected error occurred while deleting the template."})
        return RedirectResponse(url=f"/dashboard/templates?{error_msg}", status_code=303)
    finally:
        conn.close()

# ── STAFF MANAGEMENT ────────────────────────────────────────────────────────

@router.get("/staff", response_class=HTMLResponse)
async def staff_list(request: Request):
    """View list of staff members. Admin only."""
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)

    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/dashboard/", status_code=302)

    conn = get_connection()
    try:
        staff_members = conn.execute(
            "SELECT id, email, name, phone, role, is_active, last_login_at, created_at FROM admin_users ORDER BY role, email"
        ).fetchall()
        nav = get_nav_context(conn)
        return templates.TemplateResponse(
            "staff.html",
            {
                "request": request,
                "active_page": "staff",
                "staff_members": staff_members,
                "csrf_token": generate_csrf_token(),
                "user": user,
                **nav
            }
        )
    finally:
        conn.close()

@router.post("/staff/add")
async def add_staff(
    request: Request,
    email: str = Form(...),
    name: str = Form(...),
    phone: str = Form(""),
    role: str = Form("staff"),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_token: str = Form(...)
):
    """Add a new staff member. Admin only."""
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/staff?error=Invalid+CSRF+token", status_code=303)
    user = get_current_user(request)

    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/dashboard/", status_code=302)

    from urllib.parse import urlencode
    email = email.strip().lower()
    
    if not is_allowed_email(email):
        err = urlencode({"error": "Email must end with @ecosave-group.com"})
        return RedirectResponse(url=f"/dashboard/staff?{err}", status_code=303)
        
    if password != password_confirm:
        err = urlencode({"error": "Passwords do not match."})
        return RedirectResponse(url=f"/dashboard/staff?{err}", status_code=303)

    pw_errors = validate_password_strength(password)
    if pw_errors:
        err = urlencode({"error": " Password ".join(pw_errors)})
        return RedirectResponse(url=f"/dashboard/staff?{err}", status_code=303)

    hashed = hash_password(password)
    conn = get_connection()
    try:
        import sqlite3
        conn.execute(
            """INSERT INTO admin_users (email, password_hash, role, name, phone, is_active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            [email, hashed, role, name.strip(), phone.strip() or None]
        )
        conn.commit()
        return RedirectResponse(url="/dashboard/staff?success=true", status_code=303)
    except sqlite3.IntegrityError:
        err = urlencode({"error": "A user with this email already exists."})
        return RedirectResponse(url=f"/dashboard/staff?{err}", status_code=303)
    except Exception as e:
        logger.error("Error adding staff: %s", e)
        err = urlencode({"error": "An unexpected error occurred."})
        return RedirectResponse(url=f"/dashboard/staff?{err}", status_code=303)
    finally:
        conn.close()

@router.post("/staff/{id}/toggle")
async def toggle_staff_status(request: Request, id: int, csrf_token: str = Form(...)):
    """Activate or deactivate a staff member. Admin only."""
    redirect = require_login(request)
    if redirect:
        return redirect
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(url="/dashboard/staff?error=Invalid+CSRF+token", status_code=303)
    user = get_current_user(request)

    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/dashboard/", status_code=302)

    conn = get_connection()
    try:
        # Prevent deactivating oneself
        target = conn.execute("SELECT email, is_active FROM admin_users WHERE id = ?", [id]).fetchone()
        if target and target["email"] == user.get("email"):
            err = urlencode({"error": "You cannot deactivate your own account."})
            return RedirectResponse(url=f"/dashboard/staff?{err}", status_code=303)
            
        if target:
            new_status = 0 if target["is_active"] else 1
            conn.execute("UPDATE admin_users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", [new_status, id])
            conn.commit()
        return RedirectResponse(url="/dashboard/staff?success=true", status_code=303)
    finally:
        conn.close()

# ── Template Editor API Endpoints ─────────────────────────────────────────

from fastapi import Body
from utils.translate_dict import translate_text

@router.get("/api/templates/{id}")
async def api_get_template(request: Request, id: int):
    redirect = require_login(request)
    if redirect:
        return {"error": "Unauthorized"}

    conn = get_connection()
    try:
        template = conn.execute("SELECT * FROM message_templates WHERE id = ?", [id]).fetchone()
        if not template:
            return {"error": "Not found"}
        return dict(template)
    finally:
        conn.close()

@router.post("/api/templates/{id}")
async def api_save_template(request: Request, id: int, data: dict = Body(...)):
    redirect = require_login(request)
    if redirect:
        return {"error": "Unauthorized"}
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "Admin access required"})

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE message_templates SET body = ?, subject = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [data.get("body"), data.get("subject"), id]
        )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

@router.post("/api/templates/{id}/translate")
async def api_translate_template(request: Request, id: int, data: dict = Body(...)):
    redirect = require_login(request)
    if redirect:
        return {"error": "Unauthorized"}
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    body = data.get("body", "")
    translated = translate_text(body)
    return {"translated_body": translated}

@router.post("/api/templates/{id}/revert")
async def api_revert_template(request: Request, id: int):
    redirect = require_login(request)
    if redirect:
        return {"error": "Unauthorized"}
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "Admin access required"})
        
    # Default string based on typical reminders
    default_sms = "Hi {{customer_name}}, this is a reminder for your {{appointment_type}} appointment on {{appointment_date}} at {{appointment_time}}."
    default_email = "<p>Hi {{customer_name}},</p><p>This is a reminder for your <strong>{{appointment_type}}</strong> appointment on {{appointment_date}} at {{appointment_time}}.</p><p>Location: {{location}}</p>"
    
    conn = get_connection()
    try:
        t = conn.execute("SELECT channel FROM message_templates WHERE id = ?", [id]).fetchone()
        default_body = default_email if t and t["channel"] == "email" else default_sms
        return {"default_body": default_body}
    finally:
        conn.close()

@router.post("/api/templates/{id}/test-send")
async def api_test_send_template(request: Request, id: int, data: dict = Body(...)):
    redirect = require_login(request)
    if redirect:
        return {"error": "Unauthorized"}
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "Admin access required"})
        
    to = data.get("to", "").strip()
    if not to:
        return JSONResponse(status_code=400, content={"error": "No recipient provided"})

    conn = get_connection()
    try:
        tmpl = conn.execute("SELECT * FROM message_templates WHERE id = ?", [id]).fetchone()
        if not tmpl:
            return JSONResponse(status_code=404, content={"error": "Template not found"})

        sample_data = {
            "customer_name": "Test Customer",
            "appointment_type": "HVAC Installation",
            "appointment_date": "Monday, June 30",
            "appointment_time": "10:00 AM",
            "location": "123 Main St",
            "calendar_source": "hvac",
        }
        from db.templates import render_template
        body = render_template(tmpl["body"], sample_data)

        if tmpl["channel"] == "sms":
            from channels.twilio_sms import send as sms_send
            sid = sms_send(to, body, None, conn)
            return {"success": bool(sid), "message": f"Test SMS sent to {to}"}
        else:
            # queue a test email
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail
            import config
            sg = SendGridAPIClient(config.SENDGRID_API_KEY)
            msg = Mail(from_email=config.SENDGRID_FROM_EMAIL, to_emails=to,
                       subject=tmpl.get("subject") or "Test notification",
                       html_content=body)
            sg.send(msg)
            return {"success": True, "message": f"Test email sent to {to}"}
    except Exception as e:
        logger.error("Test send failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()

# ── Alert Settings API Endpoints ──────────────────────────────────────────

@router.get("/api/settings/alerts")
async def api_get_alerts(request: Request):
    redirect = require_login(request)
    if redirect:
        return {"error": "Unauthorized"}
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return {"error": "Unauthorized"}

    conn = get_connection()
    try:
        settings = Settings(conn)
        return {
            "alert_sms_enabled": settings.get("alert_sms_enabled", "true") == "true",
            "alert_email_enabled": settings.get("alert_email_enabled", "true") == "true",
            "alert_sms_from": settings.get("alert_sms_from", ""),
            "alert_sms_to": settings.get("alert_sms_to", ""),
            "alert_sms_use_staff": settings.get("alert_sms_use_staff", "true") == "true",
            "alert_email_from": settings.get("alert_email_from", ""),
            "alert_email_to": settings.get("alert_email_to", "")
        }
    finally:
        conn.close()

@router.post("/api/settings/alerts")
async def api_save_alerts(request: Request, data: dict = Body(...)):
    redirect = require_login(request)
    if redirect:
        return {"error": "Unauthorized"}
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return {"error": "Unauthorized"}

    errors = []
    sms_from = data.get("alert_sms_from", "").strip()
    sms_to_raw = data.get("alert_sms_to", "")
    email_from = data.get("alert_email_from", "").strip()
    email_to_raw = data.get("alert_email_to", "")
    
    # Validation logic
    if sms_from:
        try:
            parsed = phonenumbers.parse(sms_from, "US")
            if not phonenumbers.is_valid_number(parsed):
                errors.append(f"Invalid From number: {sms_from}")
            else:
                sms_from = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except:
            errors.append(f"Invalid From number format: {sms_from}")

    sms_to_list = []
    if sms_to_raw:
        for num in [n.strip() for n in sms_to_raw.split(",") if n.strip()]:
            try:
                parsed = phonenumbers.parse(num, "US")
                if not phonenumbers.is_valid_number(parsed):
                    errors.append(f"Invalid To number: {num}")
                else:
                    sms_to_list.append(phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164))
            except:
                errors.append(f"Invalid To number format: {num}")

    email_regex = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
    if email_from and not email_regex.match(email_from):
        errors.append(f"Invalid From email: {email_from}")
        
    email_to_list = []
    if email_to_raw:
        for em in [e.strip() for e in email_to_raw.split(",") if e.strip()]:
            if not email_regex.match(em):
                errors.append(f"Invalid To email: {em}")
            else:
                email_to_list.append(em)

    if errors:
        return {"success": False, "errors": errors}

    conn = get_connection()
    try:
        settings = Settings(conn)
        settings.set("alert_sms_enabled", "true" if data.get("alert_sms_enabled") else "false")
        settings.set("alert_email_enabled", "true" if data.get("alert_email_enabled") else "false")
        settings.set("alert_sms_from", sms_from)
        settings.set("alert_sms_to", ",".join(sms_to_list))
        settings.set("alert_sms_use_staff", "true" if data.get("alert_sms_use_staff") else "false")
        settings.set("alert_email_from", email_from)
        settings.set("alert_email_to", ",".join(email_to_list))
        return {"success": True}
    finally:
        conn.close()
