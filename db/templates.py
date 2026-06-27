"""db/templates.py — Template lookup and rendering helpers.

Lookup order (most-specific → least-specific):
  1. channel + appointment_type + language + rule_name  (exact)
  2. channel + 'all'            + language + rule_name  (type fallback)
  3. channel + 'all'            + 'en'     + rule_name  (language fallback)

render_template() performs simple {{variable}} substitution and raises
ValueError if any placeholder survives substitution (guards against
misconfigured templates going out to customers).

Rule 2: Template bodies may contain customer PII (names, times, locations).
        Never log the rendered output — only log template IDs and lookup keys.
"""

import logging
import re

logger = logging.getLogger(__name__)


def get_template(
    conn,
    channel: str,
    appointment_type: str,
    language: str,
    rule_name: str,
) -> dict | None:
    """Fetch the best-matching active message template.

    Returns a dict with keys:
        id       – integer primary key
        body     – template body string with {{placeholder}} tokens
        subject  – subject line (email only; None for SMS rows)
    Returns None if no active template exists for the given combination.

    Logs a WARNING when falling back to English so operators can add
    missing translations without the system silently degrading.
    """
    _SQL = """
        SELECT id, body, subject
        FROM message_templates
        WHERE channel          = ?
          AND appointment_type = ?
          AND language         = ?
          AND rule_name        = ?
          AND is_active        = TRUE
        LIMIT 1
    """

    def _fetch(appt_type: str, lang: str) -> dict | None:
        row = conn.execute(_SQL, [channel, appt_type, lang, rule_name]).fetchone()
        if row:
            return {"id": row["id"], "body": row["body"], "subject": row["subject"]}
        return None

    # 1. Exact match
    result = _fetch(appointment_type, language)
    if result:
        return result

    # 2. Appointment-type fallback (keep requested language)
    result = _fetch("all", language)
    if result:
        return result

    # 3. Language fallback to English
    result = _fetch("all", "en")
    if result:
        if language != "en":
            logger.warning(
                "No %r template for channel=%r rule=%r lang=%r appt_type=%r; "
                "falling back to English (template id=%d).",
                rule_name,
                channel,
                rule_name,
                language,
                appointment_type,
                result["id"],
            )
        return result

    logger.error(
        "No template found for channel=%r appt_type=%r lang=%r rule=%r.",
        channel,
        appointment_type,
        language,
        rule_name,
    )
    return None


def render_template(body: str, data: dict, html_escape_values: bool = False) -> str:
    """Replace all {{variable_name}} tokens in *body* with values from *data*.

    Supported keys in *data*:
        customer_name, appointment_type, appointment_date,
        appointment_time, location, calendar_source

    Args:
        html_escape_values: When True, HTML-escape each value before substitution
            (use for email channel to prevent XSS via calendar event data).

    Raises:
        ValueError: if any {{placeholder}} token remains after substitution,
                    which indicates a misconfigured template or missing data.
    """
    from html import escape as _html_escape
    result = body
    for key, value in data.items():
        safe_value = _html_escape(str(value)) if html_escape_values else str(value)
        result = result.replace("{{" + key + "}}", safe_value)

    # Detect any unreplaced placeholders
    remaining = re.findall(r"\{\{[^}]+\}\}", result)
    if remaining:
        raise ValueError(
            f"Template render incomplete — unreplaced placeholders: {remaining}"
        )

    return result
