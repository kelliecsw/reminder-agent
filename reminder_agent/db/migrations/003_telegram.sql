CREATE TABLE telegram_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    telegram_chat_id INTEGER NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);


CREATE INDEX idx_telegram_accounts_chat
    ON telegram_accounts(telegram_chat_id);
