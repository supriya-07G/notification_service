"""db/settings.py — Database-backed settings helper.

All other modules that need runtime settings import from here.
The system_settings table is seeded by schema.sql (Phase 1).

Rule 2: Never log or expose secret values. Settings stored here are
        operational flags (booleans, times, timezone) — not credentials.
"""

import datetime
import logging

import pytz

logger = logging.getLogger(__name__)


class Settings:
    """Read/write operational settings backed by the system_settings table."""

    def __init__(self, conn):
        self.conn = conn

    # ------------------------------------------------------------------
    # Core get / set
    # ------------------------------------------------------------------

    def get(self, key: str, default=None) -> str:
        """Return the stored string value for *key*, or *default* if absent."""
        row = self.conn.execute(
            "SELECT value FROM system_settings WHERE key=?", [key]
        ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str, updated_by: str = "system") -> None:
        """Upsert *key* = *value* into system_settings and commit."""
        self.conn.execute(
            """
            INSERT INTO system_settings (key, value, updated_by, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value      = excluded.value,
                updated_by = excluded.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            [key, value, updated_by],
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Kill switch (Rule 11)
    # ------------------------------------------------------------------

    def is_paused(self) -> bool:
        """Return True when the global kill-switch is engaged."""
        return self.get("notifications_paused", "false").lower() == "true"

    # ------------------------------------------------------------------
    # Channel flags
    # ------------------------------------------------------------------

    def is_sms_enabled(self) -> bool:
        """Return True when the SMS channel is active."""
        return self.get("sms_enabled", "true").lower() == "true"

    def is_email_enabled(self) -> bool:
        """Return True when the email channel is active."""
        return self.get("email_enabled", "true").lower() == "true"

    # ------------------------------------------------------------------
    # Per-rule flags
    # ------------------------------------------------------------------

    def is_rule_enabled(self, rule_name: str) -> bool:
        """Return True when the given reminder rule is active.

        *rule_name* must be one of: customer_72h | customer_24h | customer_2h.
        Maps to settings key:  reminder_72h_enabled, etc.
        """
        # Strip the "customer_" prefix so "customer_72h" → "72h"
        suffix = rule_name.replace("customer_", "")
        key = f"reminder_{suffix}_enabled"
        return self.get(key, "true").lower() == "true"

    # ------------------------------------------------------------------
    # Quiet hours (Rule 9)
    # ------------------------------------------------------------------

    def is_quiet_hours_active(self) -> bool:
        """Return True when the current time is OUTSIDE the allowed send window.

        Quiet hours are active when:
          - quiet_hours_enabled = 'true', AND
          - current local time < quiet_hours_start  OR  > quiet_hours_end

        If quiet_hours_enabled = 'false', always returns False (never quiet).
        """
        if self.get("quiet_hours_enabled", "true").lower() != "true":
            return False

        tz_name = self.get("timezone", "America/New_York")
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            logger.warning(
                "Unknown timezone %r in settings; falling back to America/New_York",
                tz_name,
            )
            tz = pytz.timezone("America/New_York")

        now = datetime.datetime.now(tz).time()
        start = datetime.time.fromisoformat(self.get("quiet_hours_start", "08:00"))
        end = datetime.time.fromisoformat(self.get("quiet_hours_end", "20:00"))

        if start < end:
            return not (start <= now <= end)
        elif start > end:
            return (now >= start) or (now <= end)
        else:
            return True
