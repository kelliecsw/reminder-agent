from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    llm_provider: Literal["kimicode", "cliproxyapi"] = "kimicode"
    llm_model: str | None = None
    llm_api_base: str | None = None
    llm_max_tokens: int = Field(default=8_000, ge=1)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_retry_attempts: int = Field(default=1, ge=0)
    llm_temperature: float = Field(default=0.6, ge=0, le=2)
    llm_reasoning_effort: str = "xhigh"
    llm_tool_round_limit: int = Field(default=6, ge=1)
    log_private_content: bool = False

    agent_token_threshold: int = Field(default=200_000, ge=1)
    agent_target_tokens: int = Field(default=40_000, ge=1)
    compression_step_interval: int = Field(default=100, ge=0)
    compression_turn_trigger: int = Field(default=100, ge=2)
    compression_turn_target: int = Field(default=25, ge=1)
    compression_keep_recent_turns: int = Field(default=3, ge=1)
    compression_summary_max_tokens: int = Field(default=2_000, ge=1)
    compression_summary_input_tokens: int = Field(default=50_000, ge=1)
    compression_tool_payload_max_tokens: int = Field(default=200, ge=1)

    database_path: Path = Path(".db/reminder-agent.db")
    default_timezone: str = "UTC"
    log_level: str = "INFO"
    log_file: Path = Path(".logs/reminder-agent.log")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    log_backup_count: int = Field(default=5, ge=1)
    worker_lease_seconds: float = Field(default=60.0, gt=0)
    update_poll_seconds: float = Field(default=0.25, gt=0)
    outbox_poll_seconds: float = Field(default=0.25, gt=0)
    update_max_attempts: int = Field(default=3, ge=1)
    update_retry_base_seconds: float = Field(default=5.0, gt=0)
    shutdown_timeout_seconds: float = Field(default=15.0, gt=0)

    scheduler_poll_seconds: float = Field(default=1.0, gt=0)
    scheduler_batch_size: int = Field(default=100, ge=1)
    delivery_max_attempts: int = Field(default=3, ge=1)
    delivery_retry_base_seconds: float = Field(default=30.0, gt=0)

    telegram_bot_token: SecretStr | None = None
    telegram_owner_user_id: int | None = Field(default=None, ge=1)
    telegram_application_user_id: int = Field(default=1, ge=1)

    kimicode_api_key: SecretStr | None = None
    cliproxyapi_api_key: SecretStr = SecretStr("cliproxyapi")

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        aliases = {
            "kimi": "kimicode",
            "kimicli": "kimicode",
            "proxy": "cliproxyapi",
        }
        normalized = str(value).strip().lower()
        return aliases.get(normalized, normalized)

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Unknown log level: {value}")
        return normalized

    @model_validator(mode="after")
    def validate_compression_settings(self) -> "Settings":
        if self.agent_target_tokens >= self.agent_token_threshold:
            raise ValueError(
                "AGENT_TARGET_TOKENS must be less than AGENT_TOKEN_THRESHOLD."
            )
        if self.compression_turn_target >= self.compression_turn_trigger:
            raise ValueError(
                "COMPRESSION_TURN_TARGET must be less than "
                "COMPRESSION_TURN_TRIGGER."
            )
        if self.compression_keep_recent_turns > self.compression_turn_target:
            raise ValueError(
                "COMPRESSION_KEEP_RECENT_TURNS must not exceed "
                "COMPRESSION_TURN_TARGET."
            )
        return self
