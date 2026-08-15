from collections.abc import Callable

import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import AuthSettings
from .jwks import JWKSCache, JWKSFetchError
from .models import Principal

logger = structlog.get_logger()

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


async def aclose() -> None:
    """Close the shared JWKS HTTP client — call from a service's shutdown hook."""
    if _jwks_cache is not None:
        await _jwks_cache.aclose()


async def _decode_token(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        logger.warning("auth_token_malformed", error=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc

    kid = header.get("kid")
    if not kid:
        logger.warning("auth_token_missing_kid")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token: missing kid")

    try:
        signing_key = await _get_jwks_cache().get_key(kid)
    except KeyError as exc:
        logger.warning("auth_token_unknown_kid", kid=kid)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token: unknown key") from exc
    except JWKSFetchError as exc:
        logger.warning("auth_jwks_unreachable", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth provider unreachable") from exc

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
        logger.warning("auth_token_rejected", error=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc


def _principal_from_claims(claims: dict) -> Principal:
    realm_access = claims.get("realm_access")
    roles = realm_access.get("roles") if isinstance(realm_access, dict) else None
    return Principal(
        subject=claims["sub"],
        username=claims.get("preferred_username"),
        email=claims.get("email"),
        roles=roles if isinstance(roles, list) else [],
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> Principal:
    claims = await _decode_token(credentials.credentials)
    return _principal_from_claims(claims)


def require_role(role: str) -> Callable[[Principal], Principal]:
    async def _require_role(user: Principal = Depends(get_current_user)) -> Principal:
        if not user.has_role(role):
            logger.warning("auth_role_denied", subject=user.subject, required_role=role)
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {role}")
        return user

    return _require_role
