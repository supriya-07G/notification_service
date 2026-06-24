"""tests/test_templates.py — Unit tests for db.templates.

Uses the test_db fixture (in-memory SQLite with schema.sql applied,
which includes 5 seeded templates).

Seeded templates (from schema.sql):
  ('sms', 'all', 'en', 'customer_72h', body=...)
  ('sms', 'all', 'en', 'customer_24h', body=...)
  ('sms', 'all', 'en', 'customer_2h',  body=...)
  ('sms', 'all', 'pt', 'customer_24h', body=...)
  ('sms', 'all', 'es', 'customer_24h', body=...)
"""

import pytest
from db.templates import get_template, render_template


# ---------------------------------------------------------------------------
# get_template tests
# ---------------------------------------------------------------------------

class TestGetTemplateExactMatch:
    def test_returns_exact_match(self, test_db):
        """Exact channel + appointment_type + language + rule_name match."""
        # Insert a specific template that differs from the seeded 'all' ones
        test_db.execute(
            """
            INSERT INTO message_templates
                (channel, appointment_type, language, rule_name, body)
            VALUES ('sms', 'install', 'en', 'customer_72h', 'Install-specific body {{customer_name}}')
            """
        )
        test_db.commit()

        tmpl = get_template(test_db, "sms", "install", "en", "customer_72h")
        assert tmpl is not None
        assert "Install-specific body" in tmpl["body"]

    def test_returns_dict_with_required_keys(self, test_db):
        """Returned dict always has id, body, and subject keys."""
        tmpl = get_template(test_db, "sms", "all", "en", "customer_72h")
        assert tmpl is not None
        assert "id" in tmpl
        assert "body" in tmpl
        assert "subject" in tmpl


class TestGetTemplateFallbackToAll:
    def test_falls_back_to_all_appointment_type(self, test_db):
        """When no exact appointment_type match, falls back to 'all'."""
        # 'estimate' is not seeded — should fall back to 'all'
        tmpl = get_template(test_db, "sms", "estimate", "en", "customer_72h")
        assert tmpl is not None
        assert "{{customer_name}}" in tmpl["body"]  # seeded 'all' template

    def test_falls_back_to_all_for_service(self, test_db):
        """'service' appointment type also falls back to 'all'."""
        tmpl = get_template(test_db, "sms", "service", "en", "customer_24h")
        assert tmpl is not None


class TestGetTemplateFallbackToEnglish:
    def test_falls_back_to_en_when_pt_template_missing(self, test_db):
        """Portuguese has no customer_72h template → falls back to English."""
        # Only 'pt' + 'customer_24h' is seeded; 72h is English-only
        tmpl = get_template(test_db, "sms", "all", "pt", "customer_72h")
        assert tmpl is not None
        # Should have returned the English template body
        assert "{{customer_name}}" in tmpl["body"]

    def test_falls_back_to_en_when_es_template_for_2h_missing(self, test_db):
        """Spanish has no customer_2h template → falls back to English."""
        tmpl = get_template(test_db, "sms", "all", "es", "customer_2h")
        assert tmpl is not None

    def test_returns_none_when_no_template_exists_at_all(self, test_db):
        """Returns None when there's no matching template of any kind."""
        tmpl = get_template(test_db, "email", "estimate", "en", "customer_72h")
        assert tmpl is None


class TestGetTemplateLanguagePreference:
    def test_prefers_pt_over_en_for_24h(self, test_db):
        """When a PT template exists (customer_24h), it should be returned, not EN."""
        tmpl = get_template(test_db, "sms", "all", "pt", "customer_24h")
        assert tmpl is not None
        assert "Olá" in tmpl["body"]  # PT template body starts with "Olá"

    def test_prefers_es_over_en_for_24h(self, test_db):
        """When an ES template exists (customer_24h), it should be returned, not EN."""
        tmpl = get_template(test_db, "sms", "all", "es", "customer_24h")
        assert tmpl is not None
        assert "Hola" in tmpl["body"]  # ES template body starts with "Hola"


# ---------------------------------------------------------------------------
# render_template tests
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    _FULL_DATA = {
        "customer_name": "Alice",
        "appointment_type": "estimate",
        "appointment_date": "Monday June 17",
        "appointment_time": "10:00 AM",
        "location": "123 Main St",
        "calendar_source": "hvac",
    }

    def test_substitutes_all_placeholders(self):
        """All {{variable}} tokens are replaced with their values."""
        body = (
            "Hi {{customer_name}}, your {{appointment_type}} is on "
            "{{appointment_date}} at {{appointment_time}} at {{location}}."
        )
        result = render_template(body, self._FULL_DATA)
        assert "{{" not in result
        assert "Alice" in result
        assert "estimate" in result
        assert "Monday June 17" in result
        assert "10:00 AM" in result
        assert "123 Main St" in result

    def test_uses_seeded_72h_template(self, test_db):
        """render_template works correctly with the seeded EN 72h SMS template."""
        tmpl = get_template(test_db, "sms", "all", "en", "customer_72h")
        assert tmpl is not None
        result = render_template(tmpl["body"], self._FULL_DATA)
        assert "Alice" in result
        assert "{{" not in result

    def test_raises_value_error_on_unreplaced_placeholder(self):
        """render_template raises ValueError when a placeholder has no matching key."""
        body = "Hi {{customer_name}}, your {{unknown_field}} is pending."
        data = {"customer_name": "Bob"}  # missing unknown_field
        with pytest.raises(ValueError, match="unreplaced placeholders"):
            render_template(body, data)

    def test_raises_value_error_lists_all_missing_placeholders(self):
        """ValueError message includes all unreplaced placeholder names."""
        body = "{{a}} and {{b}} missing."
        with pytest.raises(ValueError) as exc_info:
            render_template(body, {})
        assert "{{a}}" in str(exc_info.value)
        assert "{{b}}" in str(exc_info.value)

    def test_no_placeholders_returns_body_unchanged(self):
        """A template with no placeholders is returned as-is."""
        body = "This is a plain message with no substitutions."
        result = render_template(body, self._FULL_DATA)
        assert result == body

    def test_extra_data_keys_are_ignored(self):
        """Extra keys in data that don't appear in body cause no error."""
        body = "Hi {{customer_name}}!"
        result = render_template(body, self._FULL_DATA)  # _FULL_DATA has many extra keys
        assert result == "Hi Alice!"
