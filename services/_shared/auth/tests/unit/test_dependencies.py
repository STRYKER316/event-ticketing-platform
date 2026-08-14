import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from shared_auth import get_current_user, require_role
from tests.unit.conftest import AUDIENCE, ISSUER, KID, make_token


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
