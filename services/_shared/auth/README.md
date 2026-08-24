# _shared/auth

Shared FastAPI JWT-validation dependency for Keycloak-issued tokens, built once and
reused across every service (§3, §4).

## Usage

```python
from fastapi import Depends
from shared_auth import Principal, get_current_user, require_role

# settings/JWKS cache are lazily built from AUTH_* env vars on first use —
# nothing to wire up in most services. Use `shared_auth.configure(...)` to
# override them (e.g. in tests).

@app.get("/me")
async def me(user: Principal = Depends(get_current_user)):
    ...

@app.get("/organizer-only")
async def organizer_only(user: Principal = Depends(require_role("organizer"))):
    ...
```

## Config (env vars)

- `AUTH_KEYCLOAK_ISSUER` — e.g. `http://localhost:8081/realms/ticketing`
- `AUTH_EXPECTED_AUDIENCE` — e.g. `ticketing-services` (must match the realm's
  `oidc-audience-mapper` output, see `infra/keycloak/realm-export.json.template`)
- `AUTH_JWKS_CACHE_TTL_SECONDS` — default `300`
- `AUTH_JWKS_URI_OVERRIDE` — optional. JWKS is public key material, so fetching it
  doesn't need to go through the same hostname a token's `iss` claim carries. Set
  this when the service reaches Keycloak over a different network path than
  clients used to get their token — e.g. `http://keycloak:8080/realms/ticketing/
  protocol/openid-connect/certs` from inside the Docker network, while
  `AUTH_KEYCLOAK_ISSUER` stays the host-facing URL for `iss` validation.

## Testing

Part of the `/services` uv workspace (single shared `.venv`/`uv.lock` — see
`services/pyproject.toml`). Run from here or from `/services`:

```sh
uv run pytest
```

Tests mock the JWKS endpoint — no live Keycloak needed.
