-- ============================================================
-- notification_service schema — Phase 1
-- Safe to run multiple times (all CREATE TABLE IF NOT EXISTS).
-- ============================================================

-- ----- appointments -----
CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,                          -- Google Calendar event ID
    calendar_source TEXT NOT NULL,                -- hvac | solar | inspections | other
    customer_name TEXT,
    customer_phone TEXT,
    customer_email TEXT,
    technician_email TEXT,
    appointment_at TIMESTAMP NOT NULL,
    appointment_type TEXT,                        -- estimate | install | service | inspection
    location TEXT,
    notes TEXT,
    language TEXT DEFAULT 'en',                   -- en | pt | es (from [LANG:XX] tag or override)
    language_source TEXT DEFAULT 'default',       -- tag | override | default
    no_reminder BOOLEAN DEFAULT FALSE,            -- TRUE if [NO REMINDER] in title/description
    raw_title TEXT,
    raw_description TEXT,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----- appointment_quarantine -----
CREATE TABLE IF NOT EXISTS appointment_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gcal_event_id TEXT NOT NULL,
    calendar_source TEXT,
    raw_title TEXT,
    raw_description TEXT,
    appointment_at TIMESTAMP,
    quarantine_reason TEXT NOT NULL,
    -- values: missing_phone | invalid_phone | missing_name |
    --         ambiguous_customer | duplicate_appointment | parse_error
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolved_by TEXT,
    admin_notes TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_quar_gcal_event ON appointment_quarantine(gcal_event_id);

-- ----- notification_attempts -----
CREATE TABLE IF NOT EXISTS notification_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id TEXT NOT NULL REFERENCES appointments(id),
    appointment_at TIMESTAMP NOT NULL,            -- snapshot of appt time at send (for dedup)
    rule_name TEXT NOT NULL,                      -- customer_72h | customer_24h | customer_2h
    channel TEXT NOT NULL,                        -- sms | email
    to_address TEXT NOT NULL,
    provider_sid TEXT,                            -- Twilio MessageSid or SendGrid message ID
    status TEXT DEFAULT 'pending',
    -- sms:   pending → queued → sent → delivered | failed | undelivered
    -- email: pending → queued → delivered | failed | bounced
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status_updated_at TIMESTAMP,
    error_code TEXT,
    error_message TEXT,
    UNIQUE (appointment_id, rule_name, channel, appointment_at)
);

-- ----- opt_outs -----
CREATE TABLE IF NOT EXISTS opt_outs (
    phone TEXT NOT NULL,
    channel TEXT NOT NULL,                        -- sms | email | all
    opted_out_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT,                                  -- inbound_stop | manual | dashboard
    PRIMARY KEY (phone, channel)
);

-- ----- customer_preferences -----
CREATE TABLE IF NOT EXISTS customer_preferences (
    phone TEXT PRIMARY KEY,
    sms_consent BOOLEAN DEFAULT TRUE,
    email_consent BOOLEAN DEFAULT TRUE,
    language TEXT DEFAULT 'en',
    notify_72h BOOLEAN DEFAULT TRUE,
    notify_24h BOOLEAN DEFAULT TRUE,
    notify_2h BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----- inbound_messages -----
CREATE TABLE IF NOT EXISTS inbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_address TEXT NOT NULL,
    channel TEXT NOT NULL,                        -- sms
    body TEXT,
    twilio_sid TEXT UNIQUE,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    classification TEXT,
    -- confirm | reschedule_request | stop | start | question | unknown
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP,
    escalated BOOLEAN DEFAULT FALSE,
    escalated_to TEXT                             -- discord | email | null
);

-- ----- system_settings -----
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT 'system'
);

INSERT OR IGNORE INTO system_settings (key, value) VALUES
    ('notifications_paused', 'false'),
    ('sms_enabled', 'true'),
    ('email_enabled', 'true'),
    ('quiet_hours_start', '08:00'),
    ('quiet_hours_end', '20:00'),
    ('quiet_hours_enabled', 'true'),
    ('reminder_72h_enabled', 'true'),
    ('reminder_24h_enabled', 'true'),
    ('reminder_2h_enabled', 'true'),
    ('timezone', 'America/New_York');

-- ----- message_templates -----
CREATE TABLE IF NOT EXISTS message_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,                        -- sms | email
    appointment_type TEXT NOT NULL,               -- estimate | install | service | inspection | all
    language TEXT NOT NULL DEFAULT 'en',          -- en | pt | es
    rule_name TEXT NOT NULL,                      -- customer_72h | customer_24h | customer_2h
    subject TEXT,                                 -- email only
    body TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (channel, appointment_type, language, rule_name)
);

INSERT OR IGNORE INTO message_templates
    (channel, appointment_type, language, rule_name, body) VALUES
    ('sms','all','en','customer_72h',
     'Hi {{customer_name}}, reminder: your {{appointment_type}} appointment is in 3 days on {{appointment_date}} at {{appointment_time}}. Reply YES to confirm or NO to reschedule. Reply STOP to opt out.'),
    ('sms','all','en','customer_24h',
     'Hi {{customer_name}}, reminder: your {{appointment_type}} is TOMORROW at {{appointment_time}} at {{location}}. Reply YES to confirm or NO if you need to reschedule.'),
    ('sms','all','en','customer_2h',
     'Hi {{customer_name}}, your {{appointment_type}} appointment is in about 2 hours at {{appointment_time}}. See you soon!'),
    ('sms','all','pt','customer_24h',
     'Olá {{customer_name}}, lembrete: seu agendamento de {{appointment_type}} é AMANHÃ às {{appointment_time}} em {{location}}. Responda SIM para confirmar ou NÃO para reagendar.'),
    ('sms','all','es','customer_24h',
     'Hola {{customer_name}}, recordatorio: su cita de {{appointment_type}} es MAÑANA a las {{appointment_time}} en {{location}}. Responda SÍ para confirmar o NO para reprogramar.');

-- ----- email_queue -----
CREATE TABLE IF NOT EXISTS email_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id TEXT REFERENCES appointments(id),
    to_address TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    template_id INTEGER REFERENCES message_templates(id),
    template_data TEXT,                           -- JSON: {customer_name, appointment_type, ...}
    queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,                            -- NULL = not yet sent
    sg_message_id TEXT,
    error TEXT,
    attempts INTEGER DEFAULT 0
);

-- ----- indexes -----
CREATE INDEX IF NOT EXISTS idx_appointments_at ON appointments(appointment_at);
CREATE INDEX IF NOT EXISTS idx_attempts_appt ON notification_attempts(appointment_id, rule_name, channel, appointment_at);
CREATE INDEX IF NOT EXISTS idx_attempts_sid ON notification_attempts(provider_sid);
CREATE INDEX IF NOT EXISTS idx_opt_outs_phone ON opt_outs(phone, channel);
CREATE INDEX IF NOT EXISTS idx_inbound_unprocessed ON inbound_messages(processed, received_at) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_email_queue_unsent ON email_queue(sent_at, attempts) WHERE sent_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_quarantine_unresolved ON appointment_quarantine(resolved, detected_at) WHERE resolved = FALSE;
CREATE INDEX IF NOT EXISTS idx_appointments_no_reminder ON appointments(no_reminder, appointment_at);

-- ----- admin_users (Phase 4) -----
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    name TEXT DEFAULT '',
    phone TEXT,
    role TEXT CHECK(role IN ('admin','staff')) DEFAULT 'staff',
    is_active BOOLEAN DEFAULT TRUE,
    force_password_reset BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_admin_users_email ON admin_users(email);

-- ----- webhook_events (ClickUp idempotency) -----
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
