import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from shared_auth import AuthSettings
from shared_auth.jwks import JWKSCache, JWKSFetchError
from tests.unit.conftest import _public_key_to_jwk

ISSUER = "http://localhost:8081/realms/ticketing"

_TEST_PUBLIC_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()


def _settings(**overrides) -> AuthSettings:
    return AuthSettings(keycloak_issuer=ISSUER, expected_audience="ticketing-services", **overrides)


def _jwks_response(keys: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"keys": keys})


def _sig_key(kid: str) -> dict:
    return _public_key_to_jwk(_TEST_PUBLIC_KEY, kid)


async def test_fetches_and_caches_key(monkeypatch):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _jwks_response([_sig_key("key-1")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(), client=client)

    key = await cache.get_key("key-1")

    assert key.key_id == "key-1"
    assert calls["count"] == 1

    # Second lookup of the same, still-fresh key must not refetch.
    await cache.get_key("key-1")
    assert calls["count"] == 1


async def test_unknown_kid_triggers_refresh_then_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return _jwks_response([_sig_key("key-1")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(), client=client)

    with pytest.raises(KeyError):
        await cache.get_key("nonexistent-kid")


async def test_ttl_expiry_triggers_refresh():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _jwks_response([_sig_key("key-1")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(jwks_cache_ttl_seconds=0), client=client)

    await cache.get_key("key-1")
    # TTL is 0 — every lookup is stale, but the min-refetch-interval throttle
    # (1s) suppresses the immediate second fetch. Bypass it directly to prove
    # staleness alone would otherwise trigger a refetch.
    cache._last_attempt_at = 0.0
    await cache.get_key("key-1")

    assert calls["count"] == 2


async def test_missing_use_field_is_treated_as_signing_key():
    def handler(request: httpx.Request) -> httpx.Response:
        key = _sig_key("key-1")
        del key["use"]
        return _jwks_response([key])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(), client=client)

    key = await cache.get_key("key-1")

    assert key.key_id == "key-1"


async def test_non_signing_key_excluded():
    def handler(request: httpx.Request) -> httpx.Response:
        return _jwks_response([{**_sig_key("enc-key"), "use": "enc"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(), client=client)

    with pytest.raises(KeyError):
        await cache.get_key("enc-key")


async def test_fetch_failure_raises_jwks_fetch_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(), client=client)

    with pytest.raises(JWKSFetchError):
        await cache.get_key("any-kid")


async def test_malformed_response_raises_jwks_fetch_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not_keys": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(), client=client)

    with pytest.raises(JWKSFetchError):
        await cache.get_key("any-kid")


async def test_unknown_kid_burst_throttled_to_one_fetch():
    """N concurrent requests carrying the same garbage kid must not each
    trigger their own outbound fetch — the min-refetch-interval throttle
    limits this to effectively one fetch per burst."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _jwks_response([_sig_key("key-1")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = JWKSCache(_settings(), client=client)

    for _ in range(5):
        with pytest.raises(KeyError):
            await cache.get_key("garbage-kid")

    assert calls["count"] == 1


async def test_aclose_closes_client():
    client = httpx.AsyncClient()
    cache = JWKSCache(_settings(), client=client)

    await cache.aclose()

    assert client.is_closed
