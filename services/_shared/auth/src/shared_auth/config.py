from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_")

    keycloak_issuer: str
    expected_audience: str
    jwks_cache_ttl_seconds: int = 300
    # JWKS is public key material — fetching it doesn't require hitting the same
    # hostname a token's `iss` claim carries. Override when the service reaches
    # Keycloak over a different network path than clients used to get their
    # token (e.g. Docker-internal service name vs. the host-mapped port).
    jwks_uri_override: str | None = None

    @property
    def jwks_uri(self) -> str:
        return self.jwks_uri_override or f"{self.keycloak_issuer}/protocol/openid-connect/certs"
