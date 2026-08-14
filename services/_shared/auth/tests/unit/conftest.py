import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import shared_auth.dependencies as deps
from shared_auth import AuthSettings, JWKSCache, configure

ISSUER = "http://localhost:8081/realms/ticketing"
AUDIENCE = "ticketing-services"
KID = "test-key-1"


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def settings():
    return AuthSettings(keycloak_issuer=ISSUER, expected_audience=AUDIENCE)


class _StubJWKSCache(JWKSCache):
    """A JWKSCache that never hits the network — returns a fixed signing key."""

    def __init__(self, settings: AuthSettings, kid: str, public_key):
        super().__init__(settings)
        self._stub_kid = kid
        self._stub_key = jwt.PyJWK.from_dict(
            _public_key_to_jwk(public_key, kid), algorithm="RS256"
        )

    async def get_key(self, kid: str):
        if kid != self._stub_kid:
            raise KeyError(kid)
        return self._stub_key


def _public_key_to_jwk(public_key, kid: str) -> dict:
    import base64

    numbers = public_key.public_numbers()

    def _b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "use": "sig",
        "kid": kid,
        "alg": "RS256",
        "n": _b64(numbers.n),
        "e": _b64(numbers.e),
    }


@pytest.fixture
def configure_auth(settings, rsa_keypair):
    _, public_key = rsa_keypair
    jwks_cache = _StubJWKSCache(settings, KID, public_key)
    configure(settings=settings, jwks_cache=jwks_cache)
    yield
    deps._settings = None
    deps._jwks_cache = None


def make_token(rsa_keypair, *, roles=None, exp_delta=3600, aud=AUDIENCE, iss=ISSUER, kid=KID):
    private_key, _ = rsa_keypair
    now = int(time.time())
    claims = {
        "iss": iss,
        "aud": aud,
        "sub": "user-123",
        "iat": now,
        "exp": now + exp_delta,
        "preferred_username": "alice",
        "email": "alice@example.com",
        "realm_access": {"roles": roles or []},
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})
