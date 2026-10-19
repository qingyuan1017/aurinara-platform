"""Configure all three local frontends and APIs for Cognito managed login.

The script reads only public CloudFormation outputs. It updates ignored local
``.env`` files without printing their existing values or any secret material.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import boto3

REQUIRED_OUTPUTS = {
    "AuthorizeUrl",
    "CognitoDomain",
    "EdcDevelopmentSpaClientId",
    "JwtIssuer",
    "JwtJwksUrl",
    "LogoutUrl",
    "PlatformDevelopmentSpaClientId",
    "AurinaraDevelopmentSpaClientId",
    "TokenUrl",
    "UserPoolId",
}
ENV_ASSIGNMENT = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")


def stack_outputs(cloudformation: object, stack_name: str) -> dict[str, str]:
    """Return and validate the public outputs required for local managed login."""

    response = cloudformation.describe_stacks(StackName=stack_name)  # type: ignore[attr-defined]
    stacks = response.get("Stacks", [])
    if len(stacks) != 1:
        raise RuntimeError(f"Expected one CloudFormation stack named {stack_name!r}")
    outputs = {
        item["OutputKey"]: item["OutputValue"]
        for item in stacks[0].get("Outputs", [])
        if item.get("OutputKey") and item.get("OutputValue")
    }
    missing = sorted(REQUIRED_OUTPUTS - outputs.keys())
    if missing:
        raise RuntimeError(
            "The Platform stack does not expose the development clients: " + ", ".join(missing)
        )
    return outputs


def update_env_text(text: str, updates: Mapping[str, str]) -> str:
    """Replace known dotenv assignments and append missing keys deterministically."""

    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        match = ENV_ASSIGNMENT.match(line)
        key = match.group("key") if match else None
        if key in updates:
            result.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            result.append(line)
    remaining = {key: value for key, value in updates.items() if key not in seen}
    if remaining:
        if result and result[-1]:
            result.append("")
        result.append("# Cognito managed login (generated from Platform stack outputs).")
        result.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(result).rstrip() + "\n"


def configuration(
    outputs: Mapping[str, str],
    *,
    region: str,
    platform_repo: Path,
    aurinara_repo: Path,
    edc_repo: Path,
) -> dict[Path, dict[str, str]]:
    """Build the non-secret dotenv updates for the three local repositories."""

    platform_client = outputs["PlatformDevelopmentSpaClientId"]
    aurinara_client = outputs["AurinaraDevelopmentSpaClientId"]
    edc_client = outputs["EdcDevelopmentSpaClientId"]
    issuer = outputs["JwtIssuer"]
    jwks = outputs["JwtJwksUrl"]
    user_pool = outputs["UserPoolId"]
    domain = outputs["CognitoDomain"]

    return {
        platform_repo / ".env": {
            "OIDC_ISSUER": issuer,
            "OIDC_AUDIENCE": platform_client,
            "OIDC_USER_AUDIENCES": ",".join([platform_client, aurinara_client, edc_client]),
            "OIDC_JWKS_URL": jwks,
            "CORS_ORIGINS": "http://localhost:5173,http://localhost:5174",
        },
        platform_repo / "frontend" / ".env": {
            "VITE_COGNITO_REGION": region,
            "VITE_COGNITO_USER_POOL_ID": user_pool,
            "VITE_COGNITO_CLIENT_ID": platform_client,
            "VITE_OIDC_AUTHORIZE_URL": outputs["AuthorizeUrl"],
            "VITE_OIDC_TOKEN_URL": outputs["TokenUrl"],
            "VITE_OIDC_CLIENT_ID": platform_client,
            "VITE_OIDC_REDIRECT_URI": "http://localhost:5174/auth/callback",
            "VITE_OIDC_SCOPES": "openid profile email",
            "VITE_MANAGED_LOGIN_ONLY": "true",
        },
        aurinara_repo / ".env": {
            "JWT_ISSUER": issuer,
            "JWT_AUDIENCE": aurinara_client,
            "JWT_JWKS_URL": jwks,
            "JWT_ALGORITHMS": "RS256",
        },
        aurinara_repo / "frontend" / ".env": {
            "VITE_OIDC_AUTHORITY": issuer,
            "VITE_OIDC_CLIENT_ID": aurinara_client,
            "VITE_OIDC_REDIRECT_URI": "http://localhost:5173/auth/callback",
            "VITE_OIDC_LOGOUT_URI": "http://localhost:5173/login",
            "VITE_OIDC_END_SESSION_ENDPOINT": outputs["LogoutUrl"],
            "VITE_OIDC_SCOPES": "openid profile email",
        },
        edc_repo / "backend" / ".env": {
            "LOCAL_AUTH_ENABLED": "false",
            "COGNITO_REGION": region,
            "COGNITO_USER_POOL_ID": user_pool,
            "COGNITO_APP_CLIENT_ID": edc_client,
            "COGNITO_DOMAIN": domain,
            "COGNITO_REDIRECT_URI": "http://localhost:5173/auth/cognito/callback",
            "COGNITO_SCOPES": "openid email profile",
            "AUTH_ISSUER": issuer,
            "AUTH_AUDIENCE": edc_client,
            "AUTH_JWKS_URL": jwks,
            "AUTH_ALGORITHMS": "RS256",
            "AUTH_PROVIDER_NAME": "cognito",
            "PLATFORM_IDENTITY_ISSUER": issuer,
        },
        edc_repo / "frontend" / ".env": {
            "VITE_COGNITO_REGION": region,
            "VITE_COGNITO_USER_POOL_ID": user_pool,
            "VITE_COGNITO_CLIENT_ID": edc_client,
            "VITE_COGNITO_DOMAIN": domain,
            "VITE_COGNITO_REDIRECT_URI": "http://localhost:5173/auth/cognito/callback",
            "VITE_COGNITO_SCOPES": "openid email profile",
            "VITE_MANAGED_LOGIN_ONLY": "true",
        },
    }


def apply_configuration(settings: Mapping[Path, Mapping[str, str]], *, write: bool) -> None:
    """Validate targets and optionally write their dotenv updates."""

    for path, updates in settings.items():
        if not path.parent.is_dir():
            raise RuntimeError(f"Repository path is missing: {path.parent}")
        action = "updated" if write else "would update"
        print(f"{action} {path} ({', '.join(updates)})")
        if write:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(update_env_text(current, updates), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    repository = Path(__file__).resolve().parents[1]
    workspace = repository.parent
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--stack-name", default="aurinara-platform-production")
    result.add_argument("--region", default="us-east-1")
    result.add_argument("--platform-repo", type=Path, default=repository)
    result.add_argument("--aurinara-repo", type=Path, default=workspace / "aurinara")
    result.add_argument("--edc-repo", type=Path, default=workspace / "aurinara-edc")
    result.add_argument(
        "--write",
        action="store_true",
        help="Write ignored local .env files. Without this flag, only list target keys.",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Load Platform outputs and configure local managed login."""

    args = parser().parse_args(argv)
    cloudformation = boto3.client("cloudformation", region_name=args.region)
    outputs = stack_outputs(cloudformation, args.stack_name)
    settings = configuration(
        outputs,
        region=args.region,
        platform_repo=args.platform_repo.resolve(),
        aurinara_repo=args.aurinara_repo.resolve(),
        edc_repo=args.edc_repo.resolve(),
    )
    apply_configuration(settings, write=args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
