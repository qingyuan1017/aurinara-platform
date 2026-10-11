from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aurinara_platform.auth import InvalidTokenError, JwksTokenVerifier
from aurinara_platform.config import Settings


class StaticJwks:
    def __init__(self, key) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str):
        del token
        return type("SigningKey", (), {"key": self._key})()


def _token(private_pem: bytes, *, issuer: str, audience: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "sub": "subject-1",
            "aud": audience,
            "email": "person@example.test",
            "name": "Person Example",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )


def _service_token(
    private_pem: bytes,
    *,
    issuer: str,
    client_id: str,
    scope: str = "https://aurinara-platform.local/service",
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "sub": client_id,
            "client_id": client_id,
            "token_use": "access",
            "scope": scope,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )


def test_oidc_verifier_rejects_wrong_issuer_and_audience() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    settings = Settings(
        environment="test",
        oidc_issuer="https://issuer.example",
        oidc_audience="aurinara-platform",
    )
    verifier = JwksTokenVerifier(settings)
    verifier._jwks = StaticJwks(private.public_key())

    principal = verifier.verify(
        _token(
            private_pem,
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
        )
    )
    assert principal.subject == "subject-1"
    assert principal.email == "person@example.test"
    assert principal.display_name == "Person Example"

    with pytest.raises(InvalidTokenError):
        verifier.verify(
            _token(private_pem, issuer="https://wrong.example", audience=settings.oidc_audience)
        )
    with pytest.raises(InvalidTokenError):
        verifier.verify(_token(private_pem, issuer=settings.oidc_issuer, audience="wrong-audience"))


def test_oidc_verifier_accepts_only_registered_scoped_service_clients() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    settings = Settings(
        environment="test",
        oidc_issuer="https://issuer.example",
        oidc_audience="browser-client",
        oidc_service_client_ids="service-client",
    )
    verifier = JwksTokenVerifier(settings)
    verifier._jwks = StaticJwks(private.public_key())

    principal = verifier.verify(
        _service_token(private_pem, issuer=settings.oidc_issuer, client_id="service-client")
    )
    assert principal.subject == "service-client"

    with pytest.raises(InvalidTokenError):
        verifier.verify(
            _service_token(private_pem, issuer=settings.oidc_issuer, client_id="unknown-client")
        )
    with pytest.raises(InvalidTokenError):
        verifier.verify(
            _service_token(
                private_pem,
                issuer=settings.oidc_issuer,
                client_id="service-client",
                scope="wrong/scope",
            )
        )
