import asyncio
from unittest.mock import patch

import httpx
import pytest

from reminder_agent.ai.catalog import (
    ModelNotInCatalogError,
    _reset_catalog_cache,
    ensure_model_available,
    fetch_live_models,
)
from reminder_agent.config import Settings

BASE_URL = "https://models.example.com/v1"

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _settings(**overrides) -> Settings:
    values: dict[str, object] = {
        "OPENROUTER_API_KEY": "secret",
        "llm_provider": "openrouter",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _use_transport(transport: httpx.MockTransport):
    return patch(
        "reminder_agent.ai.catalog.httpx.AsyncClient",
        side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(
            **{**kwargs, "transport": transport}
        ),
    )


def test_fetch_live_models_parses_unique_sorted_ids():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "model-b"},
                    {"id": "model-a"},
                    {"id": "model-b"},
                    {"id": ""},
                ]
            },
        )

    catalog = asyncio.run(
        fetch_live_models(
            BASE_URL + "/", "secret", transport=httpx.MockTransport(handler)
        )
    )

    assert catalog.ok
    assert catalog.models == ("model-a", "model-b")
    assert requests[0].url == httpx.URL(BASE_URL + "/models")
    assert requests[0].headers["Authorization"] == "Bearer secret"


def test_fetch_live_models_omits_authorization_without_key():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "model-a"}]})

    catalog = asyncio.run(
        fetch_live_models(BASE_URL, None, transport=httpx.MockTransport(handler))
    )

    assert catalog.ok
    assert "Authorization" not in requests[0].headers


def test_fetch_live_models_maps_auth_status():
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={}))

    catalog = asyncio.run(fetch_live_models(BASE_URL, "secret", transport=transport))

    assert not catalog.ok
    assert catalog.error_kind == "auth"
    assert catalog.status == 403


def test_fetch_live_models_maps_http_status():
    transport = httpx.MockTransport(lambda request: httpx.Response(500))

    catalog = asyncio.run(fetch_live_models(BASE_URL, "secret", transport=transport))

    assert not catalog.ok
    assert catalog.error_kind == "http"
    assert catalog.status == 500


def test_fetch_live_models_maps_transport_error_to_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)

    catalog = asyncio.run(fetch_live_models(BASE_URL, "secret", transport=transport))

    assert not catalog.ok
    assert catalog.error_kind == "network"
    assert catalog.status is None


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda: httpx.Response(200, text="not-json"),
        lambda: httpx.Response(200, json={"models": [{"id": "model-a"}]}),
        lambda: httpx.Response(200, json={"data": [{"name": "model-a"}]}),
        lambda: httpx.Response(200, json={"data": []}),
    ],
)
def test_fetch_live_models_maps_malformed_payloads(response_factory):
    transport = httpx.MockTransport(lambda request: response_factory())

    catalog = asyncio.run(fetch_live_models(BASE_URL, "secret", transport=transport))

    assert not catalog.ok
    assert catalog.error_kind == "malformed"
    assert catalog.status is None


def test_ensure_model_available_raises_for_missing_model():
    _reset_catalog_cache()
    settings = _settings(llm_model="missing/model")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]}
        )
    )

    with (
        _use_transport(transport),
        pytest.raises(ModelNotInCatalogError) as excinfo,
    ):
        asyncio.run(ensure_model_available(settings))

    message = str(excinfo.value)
    assert "missing/model" in message
    assert "model-a" in message
    assert "model-b" in message


def test_ensure_model_available_accepts_configured_model():
    _reset_catalog_cache()
    settings = _settings(llm_model="model-a")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "model-a"}]})
    )

    with _use_transport(transport):
        asyncio.run(ensure_model_available(settings))


def test_ensure_model_available_fails_open_on_catalog_error():
    _reset_catalog_cache()
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)

    with _use_transport(transport):
        asyncio.run(ensure_model_available(settings))


def test_ensure_model_available_memoizes_and_refetches_on_identity_change():
    _reset_catalog_cache()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "model-a"}]})

    transport = httpx.MockTransport(handler)
    settings = _settings(llm_model="model-a")

    with _use_transport(transport):
        asyncio.run(ensure_model_available(settings))
        asyncio.run(ensure_model_available(settings))
    assert len(requests) == 1

    rotated = _settings(llm_model="model-a", OPENROUTER_API_KEY="rotated")
    with _use_transport(transport):
        asyncio.run(ensure_model_available(rotated))
    assert len(requests) == 2
    assert requests[1].headers["Authorization"] == "Bearer rotated"
