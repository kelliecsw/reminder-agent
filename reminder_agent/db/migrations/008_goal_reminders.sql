PRAGMA foreign_keys = OFF;

BEGIN;

CREATE TABLE reminders_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,

    goal_id INTEGER,
    task_id INTEGER,

    purpose TEXT NOT NULL,

    start_date_local TEXT NOT NULL,
    local_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    recurrence_json TEXT,

    next_fire_at_utc TEXT,

    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),

    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    FOREIGN KEY (goal_id)
        REFERENCES goals(id)
        ON DELETE CASCADE,

    FOREIGN KEY (task_id)
        REFERENCES tasks(id)
        ON DELETE CASCADE,

    CHECK (
        (goal_id IS NOT NULL AND task_id IS NULL)
        OR
        (goal_id IS NULL AND task_id IS NOT NULL)
    ),

    CHECK (
        (purpose = 'goal_reminder' AND goal_id IS NOT NULL)
        OR
        (purpose = 'task_reminder' AND task_id IS NOT NULL)
    )
);

INSERT INTO reminders_v2 (
    id,
    user_id,
    goal_id,
    task_id,
    purpose,
    start_date_local,
    local_time,
    timezone,
    recurrence_json,
    next_fire_at_utc,
    status,
    version,
    created_at_utc,
    updated_at_utc
)
SELECT
    id,
    user_id,
    goal_id,
    task_id,
    CASE purpose
        WHEN 'goal_check_in' THEN 'goal_reminder'
        ELSE purpose
    END,
    start_date_local,
    local_time,
    timezone,
    recurrence_json,
    next_fire_at_utc,
    status,
    version,
    created_at_utc,
    updated_at_utc
FROM reminders;

DROP TABLE reminders;
ALTER TABLE reminders_v2 RENAME TO reminders;

CREATE INDEX idx_reminders_user_status
    ON reminders(user_id, status);

CREATE INDEX idx_reminders_next_fire
    ON reminders(status, next_fire_at_utc);

CREATE INDEX idx_reminders_task
    ON reminders(task_id);

CREATE INDEX idx_reminders_goal
    ON reminders(goal_id);

COMMIT;

PRAGMA foreign_keys = ON;
