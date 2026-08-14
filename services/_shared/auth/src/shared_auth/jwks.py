import time

import httpx
from jwt import PyJWK

from .config import AuthSettings


class JWKSCache:
    """Caches a realm's signing keys by kid, refreshing on unknown kid or TTL expiry."""

    def __init__(self, settings: AuthSettings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0

    async def _fetch(self) -> None:
        client = self._client or httpx.AsyncClient()
        try:
            response = await client.get(self._settings.jwks_uri, timeout=5.0)
            response.raise_for_status()
            jwks = response.json()
        finally:
            if self._client is None:
                await client.aclose()

        self._keys = {
            key["kid"]: PyJWK.from_dict(key)
            for key in jwks["keys"]
            if key.get("use") == "sig"
        }
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str) -> PyJWK:
        is_stale = (time.monotonic() - self._fetched_at) > self._settings.jwks_cache_ttl_seconds
        if kid not in self._keys or is_stale:
            await self._fetch()
        if kid not in self._keys:
            raise KeyError(kid)
        return self._keys[kid]
