from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import AuthSettings
from .jwks import JWKSCache
from .models import Principal

_bearer_scheme = HTTPBearer(auto_error=True)

_settings: AuthSettings | None = None
_jwks_cache: JWKSCache | None = None


def configure(*, settings: AuthSettings | None = None, jwks_cache: JWKSCache | None = None) -> None:
    """Override the module-level settings/cache singletons — mainly for tests."""
    global _settings, _jwks_cache
    if settings is not None:
        _settings = settings
    if jwks_cache is not None:
        _jwks_cache = jwks_cache


def _get_settings() -> AuthSettings:
    global _settings
    if _settings is None:
        _settings = AuthSettings()
    return _settings


def _get_jwks_cache() -> JWKSCache:
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = JWKSCache(_get_settings())
    return _jwks_cache


async def _decode_token(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc

    kid = header.get("kid")
    if not kid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token: missing kid")

    try:
        signing_key = await _get_jwks_cache().get_key(kid)
    except KeyError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token: unknown key") from exc

    settings = _get_settings()
    try:
        return jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            audience=settings.expected_audience,
            issuer=settings.keycloak_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc


def _principal_from_claims(claims: dict) -> Principal:
    return Principal(
        subject=claims["sub"],
        username=claims.get("preferred_username"),
        email=claims.get("email"),
        roles=claims.get("realm_access", {}).get("roles", []),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> Principal:
    claims = await _decode_token(credentials.credentials)
    return _principal_from_claims(claims)


def require_role(role: str) -> Callable[[Principal], Principal]:
    async def _require_role(user: Principal = Depends(get_current_user)) -> Principal:
        if not user.has_role(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {role}")
        return user

    return _require_role
