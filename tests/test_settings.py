"""tests/test_settings.py — Unit tests for db.settings.Settings.

Uses the test_db fixture from conftest.py (in-memory SQLite with schema +
seed data applied).  Each test receives a fresh connection so settings
written in one test never bleed into another.
"""

import datetime
import pytest
from unittest.mock import patch

from db.settings import Settings


class TestSettingsGet:
    def test_get_returns_seeded_value(self, test_db):
        """get() returns the value seeded by schema.sql for an existing key."""
        s = Settings(test_db)
        assert s.get("notifications_paused") == "false"

    def test_get_returns_default_for_missing_key(self, test_db):
        """get() returns the caller-supplied default when the key is absent."""
        s = Settings(test_db)
        result = s.get("nonexistent_key", "my_default")
        assert result == "my_default"

    def test_get_returns_none_default_when_not_supplied(self, test_db):
        """get() returns None (not an error) when key is absent and no default given."""
        s = Settings(test_db)
        assert s.get("no_such_key") is None


class TestSettingsSet:
    def test_set_inserts_new_key(self, test_db):
        """set() persists a brand-new key so get() can retrieve it."""
        s = Settings(test_db)
        s.set("brand_new_key", "hello_world")
        assert s.get("brand_new_key") == "hello_world"

    def test_set_updates_existing_key(self, test_db):
        """set() overwrites a previously stored value for the same key."""
        s = Settings(test_db)
        s.set("notifications_paused", "true")
        assert s.get("notifications_paused") == "true"

    def test_set_records_updated_by(self, test_db):
        """set() stores the updated_by value in the database row."""
        s = Settings(test_db)
        s.set("sms_enabled", "false", updated_by="admin_dashboard")
        row = test_db.execute(
            "SELECT updated_by FROM system_settings WHERE key='sms_enabled'"
        ).fetchone()
        assert row["updated_by"] == "admin_dashboard"


class TestIsPaused:
    def test_is_paused_true_when_set(self, test_db):
        """is_paused() returns True after notifications_paused is set to 'true'."""
        s = Settings(test_db)
        s.set("notifications_paused", "true")
        assert s.is_paused() is True

    def test_is_paused_false_by_default(self, test_db):
        """is_paused() returns False with the seeded default value 'false'."""
        s = Settings(test_db)
        assert s.is_paused() is False


class TestChannelFlags:
    def test_sms_enabled_true_by_default(self, test_db):
        s = Settings(test_db)
        assert s.is_sms_enabled() is True

    def test_sms_enabled_false_when_set(self, test_db):
        s = Settings(test_db)
        s.set("sms_enabled", "false")
        assert s.is_sms_enabled() is False

    def test_email_enabled_true_by_default(self, test_db):
        s = Settings(test_db)
        assert s.is_email_enabled() is True

    def test_email_enabled_false_when_set(self, test_db):
        s = Settings(test_db)
        s.set("email_enabled", "false")
        assert s.is_email_enabled() is False


class TestIsRuleEnabled:
    def test_rule_customer_72h_enabled_by_default(self, test_db):
        s = Settings(test_db)
        assert s.is_rule_enabled("customer_72h") is True

    def test_rule_customer_24h_enabled_by_default(self, test_db):
        s = Settings(test_db)
        assert s.is_rule_enabled("customer_24h") is True

    def test_rule_customer_2h_enabled_by_default(self, test_db):
        s = Settings(test_db)
        assert s.is_rule_enabled("customer_2h") is True

    def test_rule_disabled_when_set_false(self, test_db):
        s = Settings(test_db)
        s.set("reminder_72h_enabled", "false")
        assert s.is_rule_enabled("customer_72h") is False


class TestIsQuietHoursActive:
    """Quiet window default: 08:00–20:00 America/New_York."""

    def _settings_with_time(self, test_db, hour: int, minute: int = 0) -> Settings:
        """Return a Settings instance whose datetime.datetime.now() returns
        a fixed time in America/New_York at the given hour:minute."""
        return Settings(test_db)

    def test_quiet_at_6am_before_window(self, test_db):
        """06:00 ET is before 08:00 start → quiet hours ARE active."""
        import pytz

        s = Settings(test_db)
        tz = pytz.timezone("America/New_York")
        fake_now = datetime.datetime(2024, 6, 1, 6, 0, tzinfo=tz)

        with patch("db.settings.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.time = datetime.time  # keep time.fromisoformat working
            assert s.is_quiet_hours_active() is True

    def test_not_quiet_at_10am_inside_window(self, test_db):
        """10:00 ET is inside 08:00–20:00 → quiet hours are NOT active."""
        import pytz

        s = Settings(test_db)
        tz = pytz.timezone("America/New_York")
        fake_now = datetime.datetime(2024, 6, 1, 10, 0, tzinfo=tz)

        with patch("db.settings.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.time = datetime.time
            assert s.is_quiet_hours_active() is False

    def test_not_quiet_when_quiet_hours_disabled(self, test_db):
        """When quiet_hours_enabled='false', is_quiet_hours_active() is always False."""
        import pytz

        s = Settings(test_db)
        s.set("quiet_hours_enabled", "false")

        tz = pytz.timezone("America/New_York")
        fake_now = datetime.datetime(2024, 6, 1, 3, 0, tzinfo=tz)  # 3am — would be quiet

        with patch("db.settings.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.time = datetime.time
            assert s.is_quiet_hours_active() is False
