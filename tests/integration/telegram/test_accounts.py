import sqlite3

import pytest

from reminder_agent.repositories.telegram_accounts import TelegramAccountRepository
from reminder_agent.telegram.accounts import (
    TelegramAccountService,
    TelegramAuthorizationError,
)


def test_owner_is_linked_and_chat_id_is_updated(database, user_id):
    accounts = TelegramAccountService(
        database,
        owner_telegram_user_id=12345,
        application_user_id=user_id,
    )

    assert accounts.authenticate(
        telegram_user_id=12345,
        telegram_chat_id=12345,
        chat_type="private",
    ) == user_id
    assert accounts.authenticate(
        telegram_user_id=12345,
        telegram_chat_id=54321,
        chat_type="private",
    ) == user_id

    saved = accounts.get_for_user(user_id)
    assert saved.telegram_user_id == 12345
    assert saved.telegram_chat_id == 54321


def test_unauthorized_user_and_non_private_chat_are_rejected(database, user_id):
    accounts = TelegramAccountService(
        database,
        owner_telegram_user_id=12345,
        application_user_id=user_id,
    )

    with pytest.raises(TelegramAuthorizationError):
        accounts.authenticate(
            telegram_user_id=99999,
            telegram_chat_id=99999,
            chat_type="private",
        )

    with pytest.raises(TelegramAuthorizationError):
        accounts.authenticate(
            telegram_user_id=12345,
            telegram_chat_id=-100123,
            chat_type="group",
        )


def test_telegram_mapping_is_unique_per_application_user(database, user_id):
    repo = TelegramAccountRepository()
    with database.transaction() as conn:
        repo.create(
            conn,
            user_id=user_id,
            telegram_user_id=12345,
            telegram_chat_id=12345,
        )

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as conn:
            repo.create(
                conn,
                user_id=user_id,
                telegram_user_id=67890,
                telegram_chat_id=67890,
            )
