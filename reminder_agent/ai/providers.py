"""Registry of supported OpenAI-compatible LLM providers."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr

from reminder_agent.config import Settings


@dataclass(frozen=True)
class ProviderDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]
    api_key_env: str
    settings_api_key_field: str
    settings_base_url_field: str
    default_base_url: str
    default_model: str
    live_models: bool


_PROVIDER_DEFINITIONS = (
    ProviderDefinition(
        key="kimicode",
        label="Kimi Code",
        aliases=("kimi", "kimicli"),
        api_key_env="KIMICODE_API_KEY",
        settings_api_key_field="kimicode_api_key",
        settings_base_url_field="kimicode_base_url",
        default_base_url="https://api.kimi.com/coding/v1",
        default_model="k3",
        live_models=True,
    ),
    ProviderDefinition(
        key="cliproxyapi",
        label="CLIProxyAPI",
        aliases=("proxy",),
        api_key_env="CLIPROXYAPI_API_KEY",
        settings_api_key_field="cliproxyapi_api_key",
        settings_base_url_field="cliproxyapi_base_url",
        default_base_url="http://cli-proxy-api:8317/v1",
        default_model="gpt-5.6-sol",
        live_models=True,
    ),
    ProviderDefinition(
        key="openrouter",
        label="OpenRouter",
        aliases=("router", "openrouter"),
        api_key_env="OPENROUTER_API_KEY",
        settings_api_key_field="openrouter_api_key",
        settings_base_url_field="openrouter_base_url",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-sonnet-4.6",
        live_models=True,
    ),
)

PROVIDERS: dict[str, ProviderDefinition] = {
    definition.key: definition for definition in _PROVIDER_DEFINITIONS
}

PROVIDER_ALIASES: dict[str, str] = {
    alias: definition.key
    for definition in _PROVIDER_DEFINITIONS
    for alias in definition.aliases
}

LIVE_MODEL_PROVIDERS: tuple[str, ...] = tuple(
    definition.key for definition in _PROVIDER_DEFINITIONS if definition.live_models
)


def normalize_provider_name(provider: str) -> str:
    """Return the canonical provider key for a provider name or alias."""
    normalized = provider.strip().lower()
    return PROVIDER_ALIASES.get(normalized, normalized)


def resolve_base_url(settings: Settings, provider: str) -> str:
    """Resolve the provider base URL from settings or the provider default."""
    definition = PROVIDERS[provider]
    override = getattr(settings, definition.settings_base_url_field, None)
    if override:
        return override
    return settings.llm_api_base or definition.default_base_url


def resolve_api_key(settings: Settings, provider: str) -> SecretStr | None:
    """Return the provider API key from settings, or None when unset."""
    return getattr(settings, PROVIDERS[provider].settings_api_key_field, None)


__all__ = [
    "LIVE_MODEL_PROVIDERS",
    "PROVIDERS",
    "PROVIDER_ALIASES",
    "ProviderDefinition",
    "normalize_provider_name",
    "resolve_api_key",
    "resolve_base_url",
]
