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
  `oidc-audience-mapper` output, see `infra/keycloak/realm-export.json`)
- `AUTH_JWKS_CACHE_TTL_SECONDS` — default `300`

## Testing

```sh
uv run pytest
```

Tests mock the JWKS endpoint — no live Keycloak needed.
