import pytest
from unittest.mock import patch
from workers.reply_processor import classify, run

def test_classify_confirm():
    assert classify("YES") == "confirm"
    assert classify("Ok") == "confirm"
    assert classify("Y") == "confirm"

def test_classify_stop():
    assert classify("STOP") == "stop"

def test_classify_reschedule():
    assert classify("can you reschedule me?") == "reschedule_request"
    assert classify("I need to move my appointment") == "reschedule_request"

def test_classify_question():
    assert classify("what time is my appointment?") == "question"
    assert classify("when is it?") == "question"

def test_classify_unknown():
    assert classify("blah blah") == "unknown"

def test_run_confirm(test_db, non_closing_db):
    # Insert unprocessed message with body='YES' -> processed=TRUE, classification='confirm', escalated=FALSE
    test_db.execute(
        "INSERT INTO inbound_messages (twilio_sid, channel, from_address, body) "
        "VALUES ('sid-1', 'sms', '+1234567890', 'YES')"
    )
    test_db.commit()

    with patch('workers.reply_processor.get_connection', return_value=non_closing_db), \
         patch('workers.reply_processor.send_discord_alert') as mock_send:
        run()
        mock_send.assert_not_called()

    msg = test_db.execute("SELECT * FROM inbound_messages WHERE twilio_sid='sid-1'").fetchone()
    assert msg["processed"] == 1
    assert msg["classification"] == "confirm"
    assert msg["escalated"] == 0
    assert msg["escalated_to"] is None

def test_run_escalated(test_db, non_closing_db):
    # Insert unprocessed message with body='reschedule please' -> escalated=TRUE, escalated_to='discord'
    test_db.execute(
        "INSERT INTO inbound_messages (twilio_sid, channel, from_address, body) "
        "VALUES ('sid-2', 'sms', '+1234567890', 'reschedule please')"
    )
    test_db.commit()

    with patch('workers.reply_processor.get_connection', return_value=non_closing_db), \
         patch('workers.reply_processor.send_discord_alert') as mock_send:
        run()
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        assert args[1] == "reschedule_request"

    msg = test_db.execute("SELECT * FROM inbound_messages WHERE twilio_sid='sid-2'").fetchone()
    assert msg["processed"] == 1
    assert msg["classification"] == "reschedule_request"
    assert msg["escalated"] == 1
    assert msg["escalated_to"] == "discord"
