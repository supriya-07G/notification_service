-- ============================================================
-- ClickUp Webhook Integration — Migration
-- Safe to run multiple times (all CREATE TABLE/INDEX IF NOT EXISTS).
-- ============================================================

-- ----- webhook_events (idempotency) -----
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                          -- 'clickup'
    external_event_id TEXT NOT NULL,               -- task_id + webhook_id composite
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    UNIQUE(source, external_event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_lookup
    ON webhook_events(source, external_event_id);

-- ----- audit_log -----
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,                          -- appointment_created | appointment_updated | quarantined
    source TEXT NOT NULL,                          -- clickup_webhook | manual | calendar_sync
    entity_id TEXT,                                -- appointment ID or quarantine row ID
    details TEXT,                                  -- JSON blob with context
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity
    ON audit_log(entity_id, created_at);
