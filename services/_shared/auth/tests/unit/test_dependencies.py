import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from shared_auth import JWKSFetchError, configure, get_current_user, require_role
from shared_auth.jwks import JWKSCache
from tests.unit.conftest import AUDIENCE, ISSUER, KID, make_raw_token, make_token


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_valid_token_passes(rsa_keypair):
    token = make_token(rsa_keypair, roles=["user"])

    principal = await get_current_user(_creds(token))

    assert principal.subject == "user-123"
    assert principal.username == "alice"
    assert principal.roles == ["user"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_expired_token_rejected(rsa_keypair):
    token = make_token(rsa_keypair, exp_delta=-60)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_tampered_token_rejected(rsa_keypair):
    token = make_token(rsa_keypair, roles=["organizer"])
    header, payload, signature = token.split(".")

    tampered_payload = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(b"organizer", b"attacker!")
    ).decode()
    tampered_token = f"{header}.{tampered_payload}.{signature}"

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(tampered_token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_wrong_audience_rejected(rsa_keypair):
    token = make_token(rsa_keypair, aud="some-other-service")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_wrong_issuer_rejected(rsa_keypair):
    token = make_token(rsa_keypair, iss="http://attacker.example/realms/ticketing")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_unknown_kid_rejected(rsa_keypair):
    token = make_token(rsa_keypair, kid="some-other-kid")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_missing_required_role_forbidden(rsa_keypair):
    token = make_token(rsa_keypair, roles=["user"])
    principal = await get_current_user(_creds(token))

    dependency = require_role("organizer")
    with pytest.raises(HTTPException) as exc_info:
        await dependency(principal)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_present_required_role_passes(rsa_keypair):
    token = make_token(rsa_keypair, roles=["user", "organizer"])
    principal = await get_current_user(_creds(token))

    dependency = require_role("organizer")
    result = await dependency(principal)

    assert result.subject == principal.subject


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_algorithm_confusion_attack_rejected(rsa_keypair):
    """Classic RS256->HS256 confusion: sign with the server's own (public)
    RSA key used as an HMAC secret. Must be rejected outright since
    `_decode_token` hardcodes `algorithms=["RS256"]`. PyJWT's own `encode()`
    refuses to build this token (it detects a PEM key under HS256 and raises),
    so the forged JWS is built by hand — a real attacker wouldn't go through
    PyJWT's guard rails either."""
    import hashlib
    import hmac
    import json

    _, public_key = rsa_keypair
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "attacker",
        "iat": now,
        "exp": now + 3600,
        "realm_access": {"roles": ["organizer"]},
    }
    header = {"alg": "HS256", "typ": "JWT", "kid": KID}
    header_b64 = jwt.utils.base64url_encode(json.dumps(header).encode())
    payload_b64 = jwt.utils.base64url_encode(json.dumps(claims).encode())
    signing_input = header_b64 + b"." + payload_b64
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    forged_token = (signing_input + b"." + jwt.utils.base64url_encode(signature)).decode()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(forged_token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
@pytest.mark.parametrize("missing_claim", ["exp", "iat", "sub"])
async def test_missing_required_claim_rejected(rsa_keypair, missing_claim):
    private_key, _ = rsa_keypair
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "iat": now,
        "exp": now + 3600,
        "realm_access": {"roles": []},
    }
    del claims[missing_claim]
    token = make_raw_token(claims, key=private_key)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_auth")
async def test_malformed_realm_access_defaults_to_no_roles(rsa_keypair):
    """A present-but-null `realm_access`, or a non-list `roles`, must fail
    closed (empty roles) rather than crash with a 500."""
    private_key, _ = rsa_keypair
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "iat": now,
        "exp": now + 3600,
        "realm_access": None,
    }
    token = make_raw_token(claims, key=private_key)

    principal = await get_current_user(_creds(token))

    assert principal.roles == []


@pytest.mark.asyncio
async def test_jwks_unreachable_returns_503(settings, rsa_keypair):
    class _BrokenJWKSCache(JWKSCache):
        async def get_key(self, kid: str):
            raise JWKSFetchError("simulated Keycloak outage")

    configure(settings=settings, jwks_cache=_BrokenJWKSCache(settings))
    token = make_token(rsa_keypair)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_creds(token))
    finally:
        import shared_auth.dependencies as deps

        deps._settings = None
        deps._jwks_cache = None

    assert exc_info.value.status_code == 503
