# AWS deployment: suite foundation

`aurinara-platform` owns the shared AWS foundation for the three products. Each
environment stack creates the shared Cognito user pool and product clients, the
Platform event bus, the Platform public edge, and the Platform PrivateLink
service. Aurinara and Aurinara EDC consume the published stack outputs; they do
not create independent identity providers or publish directly into product
databases.

## Required external inputs

- AWS account and `us-east-1` deployment region.
- Cloudflare zone for the base domain and a zone-scoped API token with DNS read
  and edit permissions.
- An issued ACM certificate in `us-east-1` covering the Platform, Aurinara, and
  EDC staging/production hostnames.
- Optional verified SES sender used by Cognito. When omitted, Cognito uses its
  built-in email sender and its service limits.
- Optional private malware-scanner HTTPS endpoint and a Secrets Manager secret
  holding its bearer token. Configure both values together. When omitted,
  uploaded objects are accepted without malware inspection in that environment.
- An optional private DNS override for the endpoint service. It defaults to
  `platform.internal.<base-domain>`. Cloudflare must host the generated
  PrivateLink verification TXT record.
- GitHub `staging` and `production` environments; production should require
  reviewers.

Public hostnames are:

| Environment | Platform | Aurinara | EDC |
| --- | --- | --- | --- |
| staging | `platform-staging.<base-domain>` | `staging.<base-domain>` | `edc-staging.<base-domain>` |
| production | `platform.<base-domain>` | `production.<base-domain>` | `edc.<base-domain>` |

All public application CNAMEs are Cloudflare DNS-only. CloudFront and AWS WAF
remain the HTTP edge.

## Bootstrap once

Deploy Platform's bootstrap stack before either consumer bootstrap. It owns the
single GitHub OIDC provider for the account.

```bash
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name aurinara-platform-bootstrap \
  --template-file infra/cloudformation/bootstrap.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    GitHubOwner=<owner> \
    GitHubRepository=aurinara-platform \
    DeploymentBranch=main
```

Record `GitHubOidcProviderArn`; the two consumer bootstrap stacks require it.
Configure these variables in both Platform GitHub environments from the
bootstrap outputs and approved deployment inputs:

- `AWS_DEPLOYMENT_ROLE_ARN`
- `CLOUDFORMATION_EXECUTION_ROLE_ARN`
- `APPLICATION_REPOSITORY_URI`
- `TEMPLATE_BUCKET_NAME`
- `BASE_DOMAIN`, `DNS_PROVIDER=cloudflare`, `CLOUDFLARE_ZONE_ID`
- `CERTIFICATE_ARN`, `COGNITO_DOMAIN_PREFIX`
- Optional `SES_SOURCE_ARN` and `COGNITO_FROM_ADDRESS`; configure both or
  neither.
- Optional `MALWARE_SCANNER_URL` and `MALWARE_SCANNER_TOKEN_SECRET_ARN`;
  configure both or neither. Omitting them disables malware inspection.
- Optional `PRIVATE_API_DNS_NAME` and `ALARM_EMAIL`. Without an alarm email the
  dashboards, alarms, and SNS topic are still created, but no email or budget
  subscription is created.

Store `CLOUDFLARE_API_TOKEN` as an environment secret, never as a repository
variable.

## Release flow

Merges to `main` build one immutable image, generate an SBOM, scan the image,
and deploy its digest to staging. The workflow creates infrastructure with ECS
services at zero, runs Alembic as a one-off task, activates API and publisher
services, reconciles the public Cloudflare CNAME, publishes the SPA, and runs a
health check. Production repeats the exact digest after GitHub Environment
approval and takes an RDS snapshot first.

The workflow also publishes the PrivateLink ownership TXT record and waits for
AWS verification. Verify this before starting consumer deployments:

```bash
aws ec2 describe-vpc-endpoint-service-configurations \
  --service-ids <PlatformPrivateLinkServiceId> \
  --query 'ServiceConfigurations[0].PrivateDnsNameConfiguration'
```

The state must be `verified`. The Platform stack outputs are the authoritative
inputs for consumers: `VpcId`, `VpcCidr`, the application/runner/data subnet
IDs, `PlatformPrivateLinkServiceName`, `PlatformPrivateUrl`, `EventBusName`, the
shared identity key ARN, user-pool/client IDs, token and issuer URLs, and the two
product service-client secret ARNs. Consumers create their own security groups
and data services inside the matching Platform environment's network.

## Deployment order

For each environment, deploy and verify in this order:

1. `aurinara-platform-<environment>`.
2. `aurinara-<environment>`.
3. `aurinara-edc-<environment>`.

Do not deploy a consumer until Platform health, Cognito login, PrivateLink
private DNS, and EventBridge publication have been verified. Consumer deploy
scripts query the matching Platform stack directly, preventing copied output
values from drifting.

## Recovery and operations

Production uses Multi-AZ PostgreSQL, 35-day automated backups, deletion
protection, retained KMS/S3 resources, S3 versioning/Object Lock, and AWS Backup.
Restore tests remain operator-led: perform and record an RDS point-in-time
restore and S3 restore before launch and quarterly. Application rollback uses
the previous image digest; database migrations use expand/contract and forward
repair. Never automatically restore a database during application rollback.
