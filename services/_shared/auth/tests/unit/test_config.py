from shared_auth import AuthSettings


def test_jwks_uri_derived_from_issuer_by_default():
    settings = AuthSettings(
        keycloak_issuer="http://localhost:8081/realms/ticketing",
        expected_audience="ticketing-services",
    )
    assert settings.jwks_uri == "http://localhost:8081/realms/ticketing/protocol/openid-connect/certs"


def test_jwks_uri_override_takes_precedence():
    settings = AuthSettings(
        keycloak_issuer="http://localhost:8081/realms/ticketing",
        expected_audience="ticketing-services",
        jwks_uri_override="http://keycloak:8080/realms/ticketing/protocol/openid-connect/certs",
    )
    assert settings.jwks_uri == "http://keycloak:8080/realms/ticketing/protocol/openid-connect/certs"
