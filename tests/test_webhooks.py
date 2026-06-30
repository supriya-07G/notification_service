"""tests/test_webhooks.py — Unit tests for webhook routes.

Uses FastAPI TestClient. Mocks Twilio signature validation
and DB connections so tests use the non_closing_db fixture.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import config
from adapters import clickup_webhook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(non_closing_db):
    """FastAPI TestClient with Twilio validation mocked to pass and
    get_connection() returning the non_closing_db proxy."""
    with patch("routes.sms_inbound.get_connection", return_value=non_closing_db), \
         patch("routes.status_callback.get_connection", return_value=non_closing_db), \
         patch("routes.sms_inbound.validate_twilio"), \
         patch("routes.status_callback.validate_twilio"):

        from webhook_server import app
        with TestClient(app) as tc:
            yield tc


@pytest.fixture
def strict_client(non_closing_db):
    """TestClient WITHOUT mocking Twilio validation — for 403 tests."""
    with patch("routes.sms_inbound.get_connection", return_value=non_closing_db), \
         patch("routes.status_callback.get_connection", return_value=non_closing_db):

        from webhook_server import app
        with TestClient(app) as tc:
            yield tc


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /webhooks/twilio/sms — STOP / START / general
# ---------------------------------------------------------------------------

class TestSmsInboundStop:
    def test_stop_inserts_opt_out(self, client, test_db):
        """POST with Body=STOP inserts an opt_out row for channel='sms'."""
        resp = client.post(
            "/webhooks/twilio/sms",
            data={
                "From": "+12133734253",
                "Body": "STOP",
                "MessageSid": "SM_stop_001",
            },
        )
        assert resp.status_code == 200
        assert "<Response/>" in resp.text

        row = test_db.execute(
            "SELECT * FROM opt_outs WHERE phone='+12133734253' AND channel='sms'"
        ).fetchone()
        assert row is not None
        assert row["source"] == "inbound_stop"

    def test_stopall_also_opts_out(self, client, test_db):
        """STOPALL is also treated as a stop word."""
        client.post(
            "/webhooks/twilio/sms",
            data={"From": "+12133734253", "Body": "STOPALL", "MessageSid": "SM_stopall"},
        )
        row = test_db.execute(
            "SELECT * FROM opt_outs WHERE phone='+12133734253'"
        ).fetchone()
        assert row is not None


class TestSmsInboundStart:
    def test_start_deletes_opt_out(self, client, test_db):
        """POST with Body=START removes the opt_out row."""
        # First opt out
        test_db.execute(
            "INSERT INTO opt_outs (phone, channel, source) VALUES (?, ?, ?)",
            ["+12133734253", "sms", "inbound_stop"],
        )
        test_db.commit()

        resp = client.post(
            "/webhooks/twilio/sms",
            data={
                "From": "+12133734253",
                "Body": "START",
                "MessageSid": "SM_start_001",
            },
        )
        assert resp.status_code == 200

        row = test_db.execute(
            "SELECT * FROM opt_outs WHERE phone='+12133734253' AND channel='sms'"
        ).fetchone()
        assert row is None


class TestSmsInboundGeneral:
    def test_general_message_logged(self, client, test_db):
        """Non-STOP/START messages are inserted into inbound_messages."""
        resp = client.post(
            "/webhooks/twilio/sms",
            data={
                "From": "+12133734253",
                "Body": "see you tomorrow",
                "MessageSid": "SM_gen_001",
            },
        )
        assert resp.status_code == 200

        row = test_db.execute(
            "SELECT * FROM inbound_messages WHERE twilio_sid='SM_gen_001'"
        ).fetchone()
        assert row is not None
        assert row["body"] == "see you tomorrow"
        assert row["from_address"] == "+12133734253"
        assert row["channel"] == "sms"


# ---------------------------------------------------------------------------
# /webhooks/twilio/status
# ---------------------------------------------------------------------------

class TestStatusCallback:
    def test_status_update_delivered(self, client, test_db):
        """POST with MessageStatus=delivered updates notification_attempts."""
        # Insert a notification_attempts row to update
        test_db.execute(
            "INSERT INTO appointments (id, calendar_source, customer_name, customer_phone, appointment_at, appointment_type) VALUES ('appt-1', 'x', 'x', 'x', '2024-06-16T10:00:00', 'estimate')"
        )
        test_db.execute(
            """INSERT INTO notification_attempts
               (appointment_id, appointment_at, rule_name, channel, to_address,
                provider_sid, status)
               VALUES ('appt-1', '2024-06-16T10:00:00', 'customer_24h', 'sms',
                       '+12133734253', 'SM_status_001', 'queued')"""
        )
        test_db.commit()

        resp = client.post(
            "/webhooks/twilio/status",
            data={
                "MessageSid": "SM_status_001",
                "MessageStatus": "delivered",
                "ErrorCode": "",
            },
        )
        assert resp.status_code == 200
        assert "<Response/>" in resp.text

        row = test_db.execute(
            "SELECT status FROM notification_attempts WHERE provider_sid='SM_status_001'"
        ).fetchone()
        assert row["status"] == "delivered"

    def test_status_update_failed_with_error_code(self, client, test_db):
        """Failed status stores the error_code."""
        test_db.execute(
            "INSERT INTO appointments (id, calendar_source, customer_name, customer_phone, appointment_at, appointment_type) VALUES ('appt-2', 'x', 'x', 'x', '2024-06-16T10:00:00', 'estimate')"
        )
        test_db.execute(
            """INSERT INTO notification_attempts
               (appointment_id, appointment_at, rule_name, channel, to_address,
                provider_sid, status)
               VALUES ('appt-2', '2024-06-16T10:00:00', 'customer_24h', 'sms',
                       '+12133734253', 'SM_fail_001', 'queued')"""
        )
        test_db.commit()

        client.post(
            "/webhooks/twilio/status",
            data={
                "MessageSid": "SM_fail_001",
                "MessageStatus": "failed",
                "ErrorCode": "30007",
            },
        )

        row = test_db.execute(
            "SELECT status, error_code FROM notification_attempts WHERE provider_sid='SM_fail_001'"
        ).fetchone()
        assert row["status"] == "failed"
        assert row["error_code"] == "30007"


# ---------------------------------------------------------------------------
# Signature validation (403)
# ---------------------------------------------------------------------------

class TestTwilioSignatureValidation:
    def test_invalid_signature_returns_403(self, strict_client):
        """Without a valid X-Twilio-Signature, the endpoint returns 403."""
        resp = strict_client.post(
            "/webhooks/twilio/sms",
            data={
                "From": "+12133734253",
                "Body": "hello",
                "MessageSid": "SM_bad_sig",
            },
        )
        assert resp.status_code == 403


class TestClickUpWebhookIdempotency:
    def test_payload_without_history_items_is_deduped_by_payload_hash(self, non_closing_db):
        """Webhook events without history_items should use a stable hash-based id."""
        payload = {
            "event": "taskCreated",
            "task": {
                "id": "task-123",
                "name": "Jane Doe | 123 Main St",
                "description": "",
                "custom_fields": [
                    {"id": config.CLICKUP_FIELD_NAME, "value": "Jane Doe"},
                    {"id": config.CLICKUP_FIELD_PHONE, "value": "+1 617-555-0143"},
                    {"id": config.CLICKUP_FIELD_EMAIL, "value": "jane@example.com"},
                    {
                        "id": config.CLICKUP_FIELD_SCOPE,
                        "value": ["4ddeb225-8a97-43e2-b13f-76e1ceba2421"],
                    },
                    {"id": config.CLICKUP_FIELD_DATE_HVAC, "value": 1735689600000},
                ],
            },
        }

        with patch("adapters.clickup_webhook.get_connection", return_value=non_closing_db):
            first = clickup_webhook.process_webhook(payload)
            second = clickup_webhook.process_webhook(payload)

        assert first["action"] == "created"
        assert second["action"] == "duplicate"
