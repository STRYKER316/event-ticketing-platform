import asyncio
import time

import httpx
from jwt import PyJWK
from jwt.exceptions import PyJWKError

from .config import AuthSettings

# Throttles how often an unknown/expired-cache lookup can trigger an outbound
# fetch, regardless of request volume — without this, a client sending garbage
# `kid`s can drive 1:1 request-to-Keycloak-fetch traffic.
MIN_REFETCH_INTERVAL_SECONDS = 1.0


class JWKSFetchError(Exception):
    """The JWKS endpoint couldn't be reached or returned something unusable."""


class JWKSCache:
    """Caches a realm's signing keys by kid, refreshing on unknown kid or TTL expiry."""

    def __init__(self, settings: AuthSettings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0
        self._last_attempt_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _fetch(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient()

        try:
            response = await self._client.get(self._settings.jwks_uri, timeout=5.0)
            response.raise_for_status()
            keys = response.json()["keys"]
            parsed_keys = {
                key["kid"]: PyJWK.from_dict(key)
                for key in keys
                # "use" is optional per RFC 7517 — only exclude keys explicitly
                # marked for a different purpose (e.g. encryption), don't require it.
                if key.get("use") in (None, "sig")
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, PyJWKError) as exc:
            raise JWKSFetchError(f"failed to fetch JWKS from {self._settings.jwks_uri}") from exc

        self._keys = parsed_keys
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str) -> PyJWK:
        if kid not in self._keys or self._is_stale():
            async with self._lock:
                # Re-check after acquiring the lock: a concurrent request may
                # have already refreshed the cache while this one was waiting.
                since_last_attempt = time.monotonic() - self._last_attempt_at
                if (kid not in self._keys or self._is_stale()) and since_last_attempt > MIN_REFETCH_INTERVAL_SECONDS:
                    self._last_attempt_at = time.monotonic()
                    await self._fetch()
        if kid not in self._keys:
            raise KeyError(kid)
        return self._keys[kid]

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self._settings.jwks_cache_ttl_seconds

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
