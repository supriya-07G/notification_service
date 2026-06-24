import sqlite3
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

def run_migration():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    default_settings = {
        'alert_sms_enabled': 'true',
        'alert_email_enabled': 'true',
        'alert_sms_from': config.TWILIO_SMS_NUMBER,
        'alert_sms_to': '',
        'alert_sms_use_staff': 'true',
        'alert_email_from': config.SENDGRID_FROM_EMAIL or 'notifications@ecosave-group.com',
        'alert_email_to': ''
    }

    print("Adding default staff alert settings to system_settings...")

    for key, val in default_settings.items():
        c.execute("""
            INSERT INTO system_settings (key, value, updated_by, updated_at)
            VALUES (?, ?, 'system', CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO NOTHING
        """, [key, str(val)])

    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == '__main__':
    run_migration()
