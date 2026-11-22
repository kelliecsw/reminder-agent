"""Live model catalog fetching for OpenAI-compatible providers."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import httpx

from reminder_agent.ai.providers import (
    LIVE_MODEL_PROVIDERS,
    PROVIDERS,
    normalize_provider_name,
    resolve_api_key,
    resolve_base_url,
)
from reminder_agent.config import Settings

_LOGGER = logging.getLogger(__name__)

AUTH_STATUSES = frozenset({401, 402, 403})


@dataclass(frozen=True)
class ModelCatalog:
    models: tuple[str, ...] = ()
    error_kind: str | None = None
    error: str | None = None
    status: int | None = None

    @property
    def ok(self) -> bool:
        """Whether the catalog fetch succeeded."""
        return self.error_kind is None


class ModelNotInCatalogError(RuntimeError):
    """Raised when the configured model is absent from the live catalog."""


_CATALOG_CACHE: dict[tuple[str, str], ModelCatalog] = {}


async def fetch_live_models(
    base_url: str,
    api_key: str | None,
    *,
    timeout: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelCatalog:
    """GET ``{base_url}/models`` and return model IDs plus failure metadata."""
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/models", headers=headers
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        return ModelCatalog(
            error_kind="auth" if status in AUTH_STATUSES else "http",
            error=f"model catalog request failed with HTTP {status}",
            status=status,
        )
    except httpx.TransportError as exc:
        return ModelCatalog(error_kind="network", error=str(exc) or type(exc).__name__)
    except ValueError as exc:
        return ModelCatalog(error_kind="malformed", error=str(exc))

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return ModelCatalog(
            error_kind="malformed",
            error="model catalog response does not contain a data array",
        )
    ids = [
        str(model.get("id")).strip()
        for model in data
        if isinstance(model, dict) and model.get("id")
    ]
    unique = sorted(dict.fromkeys(model_id for model_id in ids if model_id))
    if not unique:
        return ModelCatalog(
            error_kind="malformed",
            error="provider returned no usable model entries",
        )
    return ModelCatalog(models=tuple(unique))


def _catalog_identity(base_url: str, api_key: str) -> tuple[str, str]:
    credential = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return (base_url.strip().rstrip("/"), credential)


def _reset_catalog_cache() -> None:
    _CATALOG_CACHE.clear()


async def ensure_model_available(
    settings: Settings,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Verify the configured model against the live catalog, failing open."""
    provider = normalize_provider_name(settings.llm_provider)
    if provider not in LIVE_MODEL_PROVIDERS:
        return
    log = logger or _LOGGER
    base_url = resolve_base_url(settings, provider)
    api_key = resolve_api_key(settings, provider)
    key_value = api_key.get_secret_value() if api_key is not None else ""
    identity = _catalog_identity(base_url, key_value)
    catalog = _CATALOG_CACHE.get(identity)
    if catalog is None:
        catalog = await fetch_live_models(base_url, key_value or None)
        _CATALOG_CACHE[identity] = catalog
    if not catalog.ok:
        log.warning(
            "Model catalog unavailable; skipping model availability check. "
            "provider=%s kind=%s status=%s error=%s",
            provider,
            catalog.error_kind,
            catalog.status,
            catalog.error,
        )
        return
    model = settings.llm_model or PROVIDERS[provider].default_model
    log.info(
        "Model catalog refreshed. provider=%s models=%d",
        provider,
        len(catalog.models),
    )
    log.debug(
        "Model catalog contents. provider=%s models=%s",
        provider,
        ", ".join(catalog.models),
    )
    if model not in catalog.models:
        raise ModelNotInCatalogError(
            f"Model {model!r} is not available for provider {provider}. "
            f"Available models: {', '.join(catalog.models)}"
        )


__all__ = [
    "AUTH_STATUSES",
    "ModelCatalog",
    "ModelNotInCatalogError",
    "ensure_model_available",
    "fetch_live_models",
]
