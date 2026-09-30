ALTER TABLE deliveries ADD COLUMN claim_token TEXT;
ALTER TABLE deliveries ADD COLUMN claimed_at_utc TEXT;
ALTER TABLE deliveries ADD COLUMN lease_expires_at_utc TEXT;

CREATE INDEX idx_deliveries_claimable
    ON deliveries(status, deliver_at_utc, lease_expires_at_utc);


CREATE TABLE telegram_updates (
    update_id INTEGER PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    application_user_id INTEGER NOT NULL,
    update_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT,
    next_attempt_at_utc TEXT NOT NULL,
    claim_token TEXT,
    claimed_at_utc TEXT,
    lease_expires_at_utc TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,

    FOREIGN KEY (application_user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_telegram_updates_claimable
    ON telegram_updates(status, next_attempt_at_utc, lease_expires_at_utc);


CREATE TABLE telegram_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    application_user_id INTEGER NOT NULL,
    delivery_id INTEGER,
    telegram_chat_id INTEGER,
    text TEXT NOT NULL,
    keyboard_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT,
    next_attempt_at_utc TEXT NOT NULL,
    claim_token TEXT,
    claimed_at_utc TEXT,
    lease_expires_at_utc TEXT,
    telegram_message_id INTEGER,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,

    FOREIGN KEY (application_user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    FOREIGN KEY (delivery_id)
        REFERENCES deliveries(id)
        ON DELETE SET NULL
);

CREATE INDEX idx_telegram_outbox_claimable
    ON telegram_outbox(status, next_attempt_at_utc, lease_expires_at_utc);

CREATE INDEX idx_telegram_outbox_delivery
    ON telegram_outbox(delivery_id)
    WHERE delivery_id IS NOT NULL;


CREATE TABLE agent_threads (
    application_user_id INTEGER PRIMARY KEY,
    messages_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,

    FOREIGN KEY (application_user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);


CREATE TABLE tool_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX idx_tool_executions_request
    ON tool_executions(request_id);
