CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);


CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);


CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    goal_id INTEGER,
    title TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    FOREIGN KEY (goal_id)
        REFERENCES goals(id)
        ON DELETE SET NULL
);


CREATE TABLE goal_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    occurred_on TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (goal_id)
        REFERENCES goals(id)
        ON DELETE CASCADE,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);


CREATE TABLE reminders (
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
        (purpose = 'goal_check_in' AND goal_id IS NOT NULL)
        OR
        (purpose = 'task_reminder' AND task_id IS NOT NULL)
    )
);


CREATE TABLE deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    reminder_id INTEGER NOT NULL,
    reminder_version INTEGER NOT NULL CHECK (reminder_version >= 1),
    scheduled_for_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    FOREIGN KEY (reminder_id)
        REFERENCES reminders(id)
        ON DELETE CASCADE
);


CREATE INDEX idx_goals_user_status
    ON goals(user_id, status);

CREATE INDEX idx_tasks_user_status
    ON tasks(user_id, status);

CREATE INDEX idx_tasks_goal_status
    ON tasks(goal_id, status);

CREATE INDEX idx_reminders_user_status
    ON reminders(user_id, status);

CREATE INDEX idx_reminders_next_fire
    ON reminders(status, next_fire_at_utc);

CREATE INDEX idx_reminders_task
    ON reminders(task_id);

CREATE INDEX idx_reminders_goal
    ON reminders(goal_id);

CREATE INDEX idx_deliveries_reminder
    ON deliveries(reminder_id);

CREATE INDEX idx_deliveries_status
    ON deliveries(status, scheduled_for_utc);
