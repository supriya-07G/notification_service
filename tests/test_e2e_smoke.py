import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from notification_engine import run as engine_run
from workers.reply_processor import run as reply_run
from webhook_server import app
from fastapi.testclient import TestClient

client = TestClient(app)

def test_e2e_smoke(test_db, non_closing_db):
    import pytz
    tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)
    appt_time = now + timedelta(hours=24)
    
    appt_time_utc = appt_time.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    # 1. Create a mock appointment 24h from now in test_db
    test_db.execute("""
        INSERT INTO appointments (id, calendar_source, customer_name, customer_phone, appointment_at, appointment_type, language)
        VALUES ('e2e-appt', 'test-cal', 'E2E User', '+15551234567', ?, 'service', 'en')
    """, [appt_time_utc])
    test_db.commit()

    def mock_sms_send(to, body, attempt_id, conn):
        conn.execute("UPDATE notification_attempts SET provider_sid='SM_mock_123', status='queued' WHERE id=?", [attempt_id])
        conn.commit()
        return "SM_mock_123"

    # 2. Run notification_engine.run() with mocked twilio_sms.send
    with patch('notification_engine.get_connection', return_value=non_closing_db), \
         patch('notification_engine.sms_send', side_effect=mock_sms_send):
        engine_run()

    # 3. Verifies notification_attempts row created with status='queued'
    attempt = test_db.execute("SELECT * FROM notification_attempts WHERE appointment_id='e2e-appt'").fetchone()
    assert attempt is not None
    assert attempt["status"] == "queued"
    assert attempt["provider_sid"] == "SM_mock_123"

    # 4. Simulates status callback POST -> verifies status updated to 'delivered'
    # The status callback hits /webhooks/twilio/status
    with patch('routes.status_callback.get_connection', return_value=non_closing_db), \
         patch('routes.status_callback.validate_twilio'):
        resp = client.post("/webhooks/twilio/status", data={
            "MessageSid": "SM_mock_123",
            "MessageStatus": "delivered"
        })
        assert resp.status_code == 200

    attempt_after_callback = test_db.execute("SELECT * FROM notification_attempts WHERE appointment_id='e2e-appt'").fetchone()
    assert attempt_after_callback["status"] == "delivered"

    # 5. Simulates customer reply 'YES' -> verifies reply_processor classifies 'confirm'
    # Hit /webhooks/twilio/inbound
    with patch('routes.sms_inbound.get_connection', return_value=non_closing_db), \
         patch('routes.sms_inbound.validate_twilio'):
        resp = client.post("/webhooks/twilio/sms", data={
            "MessageSid": "SM_reply_yes",
            "From": "+15551234567",
            "To": "+10000000000",
            "Body": "YES"
        })
        assert resp.status_code == 200

    # Run reply processor
    with patch('workers.reply_processor.get_connection', return_value=non_closing_db), \
         patch('workers.reply_processor.send_discord_alert'):
        reply_run()

    reply_msg = test_db.execute("SELECT * FROM inbound_messages WHERE twilio_sid='SM_reply_yes'").fetchone()
    assert reply_msg["processed"] == 1
    assert reply_msg["classification"] == "confirm"

    # 6. Simulates customer reply 'STOP' -> verifies opt_out inserted
    with patch('routes.sms_inbound.get_connection', return_value=non_closing_db), \
         patch('routes.sms_inbound.validate_twilio'):
        resp = client.post("/webhooks/twilio/sms", data={
            "MessageSid": "SM_reply_stop",
            "From": "+15551234567",
            "To": "+10000000000",
            "Body": "STOP"
        })
        assert resp.status_code == 200

    opt_out = test_db.execute("SELECT * FROM opt_outs WHERE phone='+15551234567'").fetchone()
    assert opt_out is not None
    assert opt_out["channel"] == "sms"

    # 7. Runs engine again -> verifies STOP customer is skipped
    # Let's create a new 24h appointment for the same phone
    test_db.execute("""
        INSERT INTO appointments (id, calendar_source, customer_name, customer_phone, appointment_at, appointment_type, language)
        VALUES ('e2e-appt-2', 'test-cal', 'E2E User 2', '+15551234567', ?, 'service', 'en')
    """, [appt_time_utc])
    test_db.commit()

    with patch('notification_engine.get_connection', return_value=non_closing_db), \
         patch('notification_engine.sms_send') as mock_send:
        engine_run()
        mock_send.assert_not_called()

    attempt2 = test_db.execute("SELECT * FROM notification_attempts WHERE appointment_id='e2e-appt-2'").fetchone()
    assert attempt2 is not None
    assert attempt2["status"] == "skipped_optout"
