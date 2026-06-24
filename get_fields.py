"""get_fields.py — Print all custom field IDs for a ClickUp list.

Reads CLICKUP_API_TOKEN from .env via config.py.
Usage: python get_fields.py
"""
from dotenv import load_dotenv
load_dotenv()

import sys
import requests
import config

if not config.CLICKUP_API_TOKEN:
    print("ERROR: CLICKUP_API_TOKEN is not set in .env")
    sys.exit(1)

LIST_ID = "901317175958"

r = requests.get(
    f"https://api.clickup.com/api/v2/list/{LIST_ID}/field",
    headers={"Authorization": config.CLICKUP_API_TOKEN},
    timeout=10,
)

if not r.ok:
    print(f"ERROR: API returned {r.status_code}: {r.text}")
    sys.exit(1)

fields = r.json().get("fields", [])
print(f"Found {len(fields)} custom fields in list {LIST_ID}:\n")
for f in fields:
    print(f"  Name: {f['name']}")
    print(f"  ID:   {f['id']}")
    print(f"  Type: {f.get('type', 'unknown')}")
    if f.get("type") == "drop_down":
        opts = f.get("type_config", {}).get("options", [])
        print(f"  Options: {[o['name'] for o in opts]}")
    print()
