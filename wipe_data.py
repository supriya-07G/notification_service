import sqlite3
import os

DB_PATH = 'storage.sqlite'

if not os.path.exists(DB_PATH):
    print(f"Database file not found: {DB_PATH}")
    exit(1)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

try:
    print("Wiping appointments, appointment_quarantine, and clickup webhook events...")
    
    # Delete appointments and quarantine
    cursor.execute("DELETE FROM appointments;")
    print(f"Deleted {cursor.rowcount} appointments.")
    
    cursor.execute("DELETE FROM appointment_quarantine;")
    print(f"Deleted {cursor.rowcount} quarantined appointments.")
    
    cursor.execute("DELETE FROM webhook_events WHERE source = 'clickup';")
    print(f"Deleted {cursor.rowcount} clickup webhook events.")
    
    cursor.execute("DELETE FROM notification_attempts;")
    print(f"Deleted {cursor.rowcount} notification attempts.")
    
    conn.commit()
    print("Data successfully wiped.")
    
except Exception as e:
    conn.rollback()
    print(f"Error wiping data: {e}")
finally:
    conn.close()
