"""Publish and verify the PrivateLink private-DNS ownership TXT record."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from urllib import parse

from scripts.cloudflare_dns import CloudflareDnsError, cloudflare_request, read_stack_output


def private_dns_configuration(service_id: str) -> tuple[str, str, str]:
    completed = subprocess.run(
        [
            "aws",
            "ec2",
            "describe-vpc-endpoint-service-configurations",
            "--service-ids",
            service_id,
            "--query",
            "ServiceConfigurations[0].PrivateDnsNameConfiguration.[Name,Value,State]",
            "--output",
            "text",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fields = completed.stdout.strip().split()
    if len(fields) != 3:
        raise CloudflareDnsError("PrivateLink did not return a private-DNS verification record")
    return fields[0].rstrip("."), fields[1], fields[2]


def configure_private_dns(stack_name: str, zone_id: str, api_token: str) -> str:
    service_id = read_stack_output(stack_name, "PlatformPrivateLinkServiceId")
    name, value, state = private_dns_configuration(service_id)
    if state == "verified":
        return f"PrivateLink private DNS {name} is already verified"
    api_base = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    query = parse.urlencode({"type": "TXT", "name": name})
    listed = cloudflare_request("GET", f"{api_base}?{query}", api_token)
    records = listed.get("result")
    if not isinstance(records, list) or len(records) > 1:
        raise CloudflareDnsError(f"Cloudflare returned invalid TXT state for {name}")
    payload = {
        "type": "TXT",
        "name": name,
        "content": value,
        "ttl": 60,
        "comment": f"PrivateLink verification managed by {stack_name}",
    }
    if records:
        record_id = records[0].get("id")
        if not record_id:
            raise CloudflareDnsError("Existing verification TXT record has no ID")
        cloudflare_request("PUT", f"{api_base}/{record_id}", api_token, payload)
    else:
        cloudflare_request("POST", api_base, api_token, payload)
    subprocess.run(
        [
            "aws",
            "ec2",
            "start-vpc-endpoint-service-private-dns-verification",
            "--service-id",
            service_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for _ in range(80):
        _, _, state = private_dns_configuration(service_id)
        if state == "verified":
            return f"PrivateLink private DNS {name} verified"
        if state == "failed":
            raise CloudflareDnsError("PrivateLink private-DNS verification failed")
        time.sleep(15)
    raise CloudflareDnsError("Timed out waiting for PrivateLink private-DNS verification")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stack_name")
    args = parser.parse_args()
    zone_id = os.environ.get("CLOUDFLARE_ZONE_ID", "")
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not zone_id or not api_token:
        raise CloudflareDnsError("CLOUDFLARE_ZONE_ID and CLOUDFLARE_API_TOKEN are required")
    print(configure_private_dns(args.stack_name, zone_id, api_token))


if __name__ == "__main__":
    main()
