from pathlib import Path

import pytest

from scripts.configure_local_managed_login import configuration, stack_outputs, update_env_text

OUTPUTS = {
    "AuthorizeUrl": "https://login.example/oauth2/authorize",
    "CognitoDomain": "login.example",
    "EdcDevelopmentSpaClientId": "edc-development",
    "JwtIssuer": "https://issuer.example/pool",
    "JwtJwksUrl": "https://issuer.example/pool/.well-known/jwks.json",
    "LogoutUrl": "https://login.example/logout",
    "PlatformDevelopmentSpaClientId": "platform-development",
    "AurinaraDevelopmentSpaClientId": "aurinara-development",
    "TokenUrl": "https://login.example/oauth2/token",
    "UserPoolId": "us-east-1_example",
}


class CloudFormationStub:
    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs

    def describe_stacks(self, *, StackName: str) -> dict[str, object]:
        assert StackName == "platform-production"
        return {
            "Stacks": [
                {
                    "Outputs": [
                        {"OutputKey": key, "OutputValue": value}
                        for key, value in self.outputs.items()
                    ]
                }
            ]
        }


def test_stack_outputs_requires_all_development_clients() -> None:
    assert stack_outputs(CloudFormationStub(OUTPUTS), "platform-production") == OUTPUTS
    missing = dict(OUTPUTS)
    missing.pop("EdcDevelopmentSpaClientId")
    with pytest.raises(RuntimeError, match="EdcDevelopmentSpaClientId"):
        stack_outputs(CloudFormationStub(missing), "platform-production")


def test_update_env_text_preserves_unrelated_values_and_appends_missing_keys() -> None:
    result = update_env_text(
        "DATABASE_PASSWORD=keep-me\nOIDC_AUDIENCE=old-client\nOIDC_AUDIENCE=older-client\n",
        {"OIDC_AUDIENCE": "new-client", "OIDC_ISSUER": "https://issuer.example"},
    )
    assert "DATABASE_PASSWORD=keep-me" in result
    assert result.count("OIDC_AUDIENCE=new-client") == 2
    assert "old-client" not in result
    assert result.endswith("OIDC_ISSUER=https://issuer.example\n")


def test_configuration_uses_separate_clients_and_local_callbacks(tmp_path: Path) -> None:
    settings = configuration(
        OUTPUTS,
        region="us-east-1",
        platform_repo=tmp_path / "platform",
        aurinara_repo=tmp_path / "aurinara",
        edc_repo=tmp_path / "edc",
    )
    platform_frontend = settings[tmp_path / "platform" / "frontend" / ".env"]
    aurinara_backend = settings[tmp_path / "aurinara" / ".env"]
    aurinara_frontend = settings[tmp_path / "aurinara" / "frontend" / ".env"]
    edc_frontend = settings[tmp_path / "edc" / "frontend" / ".env"]
    assert platform_frontend["VITE_OIDC_CLIENT_ID"] == "platform-development"
    assert platform_frontend["VITE_MANAGED_LOGIN_ONLY"] == "true"
    assert aurinara_frontend["VITE_OIDC_CLIENT_ID"] == "aurinara-development"
    assert aurinara_frontend["VITE_OIDC_REDIRECT_URI"] == ("http://localhost:5175/auth/callback")
    assert aurinara_frontend["VITE_OIDC_LOGOUT_URI"] == "http://localhost:5175/login"
    assert aurinara_backend["APP_CORS_ALLOW_ORIGINS"] == (
        "http://localhost:5175,http://127.0.0.1:5175"
    )
    assert edc_frontend["VITE_COGNITO_CLIENT_ID"] == "edc-development"
    assert edc_frontend["VITE_COGNITO_REDIRECT_URI"] == (
        "http://localhost:5173/auth/cognito/callback"
    )
    assert edc_frontend["VITE_MANAGED_LOGIN_ONLY"] == "true"
    assert settings[tmp_path / "edc" / "backend" / ".env"]["LOCAL_AUTH_ENABLED"] == "false"
    assert settings[tmp_path / "platform" / ".env"]["CORS_ORIGINS"] == (
        "http://localhost:5173,http://localhost:5174,http://localhost:5175"
    )
