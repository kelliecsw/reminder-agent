from pathlib import Path

from reminder_agent.config import Settings


def test_default_database_path_uses_database_directory():
    settings = Settings(_env_file=None)

    assert settings.database_path == Path(".db/reminder-agent.db")


def test_settings_read_unprefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", "custom.db")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Asia/Singapore")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "90")
    monkeypatch.setenv("AGENT_TOKEN_THRESHOLD", "90000")
    monkeypatch.setenv("AGENT_TARGET_TOKENS", "30000")
    monkeypatch.setenv("LOG_FILE", "var/test.log")
    monkeypatch.setenv("LOG_MAX_BYTES", "2048")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "2")

    settings = Settings(_env_file=None)

    assert settings.database_path == Path("custom.db")
    assert settings.default_timezone == "Asia/Singapore"
    assert settings.worker_lease_seconds == 90
    assert settings.agent_token_threshold == 90_000
    assert settings.agent_target_tokens == 30_000
    assert settings.log_file == Path("var/test.log")
    assert settings.log_max_bytes == 2048
    assert settings.log_backup_count == 2
