from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_")

    keycloak_issuer: str
    expected_audience: str
    jwks_cache_ttl_seconds: int = 300

    @property
    def jwks_uri(self) -> str:
        return f"{self.keycloak_issuer}/protocol/openid-connect/certs"
