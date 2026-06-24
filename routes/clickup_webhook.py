"""routes/clickup_webhook.py — ClickUp webhook route.

POST /webhooks/clickup   — receives ClickUp task webhooks
GET  /webhooks/clickup/health — health check
"""

import logging

from fastapi import APIRouter, Request, Response

import config
from adapters.clickup_webhook import verify_signature, process_webhook

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/clickup")
async def handle_clickup_webhook(request: Request):
    """Receive and process ClickUp webhook events.

    1. Read raw body for signature verification.
    2. Validate HMAC-SHA256 signature — reject with 403 if invalid.
    3. Parse JSON and delegate to the adapter.
    4. Always return 200 OK (even on errors) so ClickUp doesn't retry.
    """
    raw_body = await request.body()

    # Signature verification
    signature = request.headers.get("X-Signature", "")
    if not verify_signature(raw_body, signature, config.CLICKUP_WEBHOOK_SECRET):
        logger.warning("ClickUp webhook rejected: invalid signature")
        return Response(content='{"error": "invalid signature"}', status_code=403,
                        media_type="application/json")

    # Parse payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Failed to parse ClickUp webhook JSON: %s", e)
        return Response(content='{"status": "ok"}', status_code=200,
                        media_type="application/json")

    # Process
    try:
        result = process_webhook(payload)
        logger.info("ClickUp webhook processed: %s", result)
    except Exception as e:
        logger.error("Unhandled error in ClickUp webhook processing: %s", e, exc_info=True)
        result = {"status": "ok", "action": "error"}

    # Always return 200
    return Response(
        content='{"status": "ok"}',
        status_code=200,
        media_type="application/json",
    )


@router.get("/webhooks/clickup/health")
async def clickup_health():
    """Simple health check for the ClickUp webhook endpoint."""
    return {"status": "ok", "endpoint": "clickup_webhook"}
