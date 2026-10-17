"""OIDC authentication boundary."""

from dataclasses import dataclass
from typing import Protocol

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

from aurinara_platform.config import Settings


class InvalidTokenError(RuntimeError):
    """Raised when an access token cannot be trusted."""


@dataclass(frozen=True, slots=True)
class Principal:
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class JwksTokenVerifier:
    """Validate an OIDC access token locally through the issuer's JWKS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks = PyJWKClient(settings.oidc_jwks_url, cache_keys=True)

    def verify(self, token: str) -> Principal:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._settings.oidc_algorithm_list,
                issuer=self._settings.oidc_issuer,
                options={"require": ["iss", "sub", "iat", "exp"], "verify_aud": False},
                leeway=self._settings.oidc_clock_skew_seconds,
            )
        except PyJWTError as error:
            raise InvalidTokenError("The bearer token is invalid or expired") from error
        issuer = claims.get("iss")
        subject = claims.get("sub")
        if not isinstance(issuer, str) or not isinstance(subject, str) or not subject:
            raise InvalidTokenError("The bearer token has invalid identity claims")
        if claims.get("token_use") == "access":
            self._validate_service_token(claims, subject)
        else:
            self._validate_user_token(claims)
        email = claims.get("email")
        display_name = claims.get("name") or claims.get("preferred_username")
        return Principal(
            issuer=issuer,
            subject=subject,
            email=email if isinstance(email, str) else None,
            display_name=display_name if isinstance(display_name, str) else None,
        )

    def _validate_user_token(self, claims: dict[str, object]) -> None:
        audience = claims.get("aud")
        audiences = {audience} if isinstance(audience, str) else set(audience or [])
        if not audiences.intersection(self._settings.oidc_user_audience_set):
            raise InvalidTokenError("The bearer token has an invalid audience")
        token_use = claims.get("token_use")
        if token_use not in {None, "id"}:
            raise InvalidTokenError("The bearer token has an invalid token use")

    def _validate_service_token(self, claims: dict[str, object], subject: str) -> None:
        client_id = claims.get("client_id")
        if (
            not isinstance(client_id, str)
            or client_id not in self._settings.oidc_service_client_id_set
            or subject != client_id
        ):
            raise InvalidTokenError("The service token has an invalid client")
        scopes = set(str(claims.get("scope", "")).split())
        if self._settings.oidc_service_scope not in scopes:
            raise InvalidTokenError("The service token is missing the required scope")
        expected_audience = self._settings.oidc_service_audience
        audience = claims.get("aud")
        if expected_audience and audience is not None:
            audiences = {audience} if isinstance(audience, str) else set(audience or [])
            if expected_audience not in audiences:
                raise InvalidTokenError("The service token has an invalid audience")
