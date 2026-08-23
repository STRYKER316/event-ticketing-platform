from typing import Annotated

from pydantic import StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_")

    keycloak_issuer: NonBlankStr
    expected_audience: NonBlankStr
    jwks_cache_ttl_seconds: int = 300
    # Override when the service reaches Keycloak over a different network path than clients do (e.g. Docker-internal name vs. host-mapped port).
    jwks_uri_override: NonBlankStr | None = None

    @field_validator("keycloak_issuer")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def jwks_uri(self) -> str:
        return self.jwks_uri_override or f"{self.keycloak_issuer}/protocol/openid-connect/certs"
