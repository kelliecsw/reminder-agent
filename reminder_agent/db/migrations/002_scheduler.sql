ALTER TABLE deliveries RENAME TO deliveries_v1;


CREATE TABLE deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    reminder_id INTEGER NOT NULL,
    reminder_version INTEGER NOT NULL CHECK (reminder_version >= 1),
    scheduled_for_utc TEXT NOT NULL,
    deliver_at_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT,
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    FOREIGN KEY (reminder_id)
        REFERENCES reminders(id)
        ON DELETE CASCADE,

    UNIQUE (reminder_id, reminder_version, scheduled_for_utc)
);


INSERT INTO deliveries (
    id,
    user_id,
    reminder_id,
    reminder_version,
    scheduled_for_utc,
    deliver_at_utc,
    status,
    attempt_count,
    last_error,
    created_at_utc,
    updated_at_utc
)
SELECT
    id,
    user_id,
    reminder_id,
    reminder_version,
    scheduled_for_utc,
    scheduled_for_utc,
    status,
    0,
    NULL,
    created_at_utc,
    updated_at_utc
FROM deliveries_v1;


DROP TABLE deliveries_v1;


CREATE INDEX idx_deliveries_reminder
    ON deliveries(reminder_id);

CREATE INDEX idx_deliveries_ready
    ON deliveries(status, deliver_at_utc);
