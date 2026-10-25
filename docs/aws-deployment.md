# AWS deployment: suite foundation

`aurinara-platform` owns the shared AWS foundation for the three products. Each
environment stack creates the shared Cognito user pool and product clients, the
Platform event bus, and the Platform public edge. The `standard` profile also
creates the Platform PrivateLink service. Aurinara and Aurinara EDC consume the published stack outputs; they do
not create independent identity providers or publish directly into product
databases.

The production workflow uses the supported `budget` profile. It shares the
Platform ALB and WAF across all three CloudFront distributions, uses one NAT
gateway, retains separate Single-AZ databases, and schedules retry-safe consumer
workers rather than keeping them continuously running. Staging is opt-in from
`workflow_dispatch`. See [Budget AWS deployment profile](budget-deployment-profile.md)
for topology, cost assumptions, cutover order, and rollback.

Platform also owns the suite CloudTrail resources. The production trail records
management events and S3 object data events for suite buckets whose names start
with `aurinara-`;
the staging trail retains Platform-staging object coverage. Consumer stacks do
not create trails, avoiding the non-adjustable five-trails-per-region limit.

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
- For `standard`, an optional private DNS override for the endpoint service. It defaults to
  `platform.internal.<base-domain>`. Cloudflare must host the generated
  PrivateLink verification TXT record.
- GitHub `staging` and `production` environments; production should require
  reviewers.

Public hostnames are:

| Environment | Platform | Aurinara | EDC |
| --- | --- | --- | --- |
| staging | `platform-staging.<base-domain>` | `staging.<base-domain>` | `edc-staging.<base-domain>` |
| production | `platform.<base-domain>` | `nara.<base-domain>` | `auri.<base-domain>` |

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

Merges to `main` build immutable application and workspace images, generate an
SBOM and vulnerability scan for each, and deploy their digests to production after approval. A manual run with
`deploy_staging=true` deploys staging first. The workflow creates infrastructure
with ECS services at zero, runs Alembic as a one-off task, activates API and publisher
services, reconciles the public Cloudflare CNAME, publishes the SPA, and runs a
health check. Production takes an RDS snapshot before the update.

### Personal workspaces

Platform owns the shared workspace control plane for all three products. Each
eligible user receives one retained 5 GB workspace per product. An active
session is a private, non-root Fargate task with an unauthenticated code-server
container and a separate synchronization sidecar. Only the API security group
may reach port 8080; task egress is limited to VPC DNS and HTTPS.

The IDE container has no task role. Platform assumes the dedicated sync role
with an inline policy restricted to the user's generated S3 prefix and delivers
temporary credentials only to the sidecar through a per-run encrypted S3
environment file. The file is removed after the task reaches `RUNNING` and is
also covered by one-day lifecycle cleanup. Workspace data is kept in the
retained, KMS-encrypted, versioned workspace bucket; noncurrent data versions
expire after 30 days.

Sessions have a two-hour hard limit, synchronize every minute, and allow 120
seconds for the final upload. The API limits each environment to ten active
workspace tasks by default. The browser receives only a single-use, 60-second
launch grant; the proxy exchanges it for a secure path-scoped HTTP-only cookie.
Consumer frontends deploy only after the Platform migration and workspace
runtime have been verified.

For local development, start the three loopback-only code-server instances and
their Repository synchronizers with:

```bash
docker compose --profile workspace up -d --build \
  workspace-platform workspace-platform-repository-sync \
  workspace-aurinara workspace-aurinara-repository-sync \
  workspace-edc workspace-edc-repository-sync
```

Each IDE uses an isolated named volume. The `/workspace/repository` directory is
an authorized logical working copy of the central Repository stored in S3 or
MinIO, organized by study and Repository folders. Existing writable files are
published back as new immutable Repository versions during synchronization;
internal object keys and storage credentials are never exposed to the IDE.
code-server settings and extensions live in a separate private state directory,
so `.aurinara/code-server` is not shown inside the workspace.

Local API configuration must include `WORKSPACE_ENABLED=true`,
`WORKSPACE_RUNTIME_MODE=static`, and `WORKSPACE_PUBLIC_BASE_URL` matching the
API origin before the API is restarted. Run `alembic upgrade head` after pulling
workspace changes; a missing workspace route or table is surfaced by the UI with
a retry action instead of an indefinite loading state.

In the `standard` profile the workflow also publishes the PrivateLink ownership
TXT record and waits for AWS verification. Verify this before starting standard
consumer deployments:

```bash
aws ec2 describe-vpc-endpoint-service-configurations \
  --service-ids <PlatformPrivateLinkServiceId> \
  --query 'ServiceConfigurations[0].PrivateDnsNameConfiguration'
```

The state must be `verified`. Budget consumers instead use the shared internal
ALB URL and listener outputs and do not create PrivateLink endpoints. The
Platform stack outputs are the authoritative
inputs for consumers: `VpcId`, `VpcCidr`, the application/runner/data subnet
IDs, `PlatformPrivateUrl`, the shared ALB/WAF/VPC-origin values, `EventBusName`, the
shared identity key ARN, user-pool/client IDs, token and issuer URLs, and the two
product service-client secret ARNs. Consumers create their own security groups
and data services inside the matching Platform environment's network.
`PlatformPrivateLinkServiceName` is additionally present in `standard`.

Browser clients use the Cognito ID token as their API bearer because it carries
the public SPA-client audience and user identity. Cognito access tokens are not
interchangeable: Platform reserves their `client_id` validation path for
registered service clients. The console stores only a same-origin return path
with the PKCE transaction and restores it after a successful callback.

### Local managed-login clients

The production user pool also owns three public development SPA clients, one per
product. They have no client secret, use authorization code + PKCE, and accept
only the documented localhost callback and logout URLs. Their audiences remain
separate from the deployed SPA clients and are not added to production product
API audience settings.

The production stack exports `PlatformDevelopmentSpaClientId`,
`AurinaraDevelopmentSpaClientId`, and `EdcDevelopmentSpaClientId`. From a local
Platform checkout, configure the ignored local API/frontend environments for all
three repositories with:

| Application | Local frontend | Callback path |
| --- | --- | --- |
| Aurinara EDC | `http://localhost:5173` | `/auth/cognito/callback` |
| Aurinara Platform | `http://localhost:5174` | `/auth/callback` |
| Aurinara | `http://localhost:5175` | `/auth/callback` |

```powershell
python scripts/configure_local_managed_login.py --write
```

The script reads public stack outputs, preserves unrelated dotenv settings, sets
the local APIs to validate the matching development audience, and enables
managed-login-only UI behavior. It does not retrieve service-client secrets.

### Automatic create-only registration

After Cognito confirms a new user, its post-confirmation Lambda writes a minimal
versioned identity message to the environment's KMS-encrypted registration queue.
The Platform `identity-registration` worker validates the exact issuer and creates
the user, its non-assignable `Registered Study Creator` role assignment, and the
normal outbox events in one database transaction. That role contains only
`platform.study.create`; it grants no user administration, product-wide access, or
visibility into an existing study. Duplicate trigger delivery is idempotent by
`(issuer, subject)`; failed messages retry and move to the encrypted dead-letter queue
after five receives.

The deployment migration task also lists confirmed, enabled users from the exact
environment user pool and reconciles missing identities before the new services roll
out. This backfills users created before the trigger was installed and remains safe to
rerun. The Aurinara and EDC migration tasks synchronize reviewed, stable study-owner
roles. A canonical study is created only when all required roles are available; the
same Platform transaction creates study-scoped assignments for the creator and emits
one product membership event for each consumer. Existing studies remain invisible
until an administrator or study owner grants an explicit membership.

### Initial administrator

Production never grants access merely because an identity is the first person
to sign in. After the selected administrator has a confirmed Cognito account,
an operator runs the explicit bootstrap command inside the deployed migration
task boundary:

```bash
python scripts/bootstrap_initial_admin.py \
  --subject <immutable-cognito-subject> \
  --email <verified-email> \
  --display-name <reviewed-display-name> \
  --confirm-production
```

The command is idempotent, refuses to register a different second human as the
"initial" administrator, assigns the reviewed Platform Administrator and EDC
System Administrator roles, and publishes the normal Platform assignment event
for the EDC projection. Do not use email as the identity key; verify the exact
Cognito `sub` before running the command.

## Deployment order

For each environment, deploy and verify in this order:

1. `aurinara-platform-<environment>`.
2. `aurinara-<environment>`.
3. `aurinara-edc-<environment>`.

Do not deploy a consumer until Platform health, Cognito login, EventBridge
publication, and the selected private path have been verified. For `standard`,
verify PrivateLink private DNS; for `budget`, verify the shared ALB and VPC-origin
outputs. Consumer deploy
scripts query the matching Platform stack directly, preventing copied output
values from drifting.

## Recovery and operations

Standard production uses Multi-AZ PostgreSQL and 35-day automated backups.
Budget production uses separate Single-AZ databases and 14-day automated
backups. Both profiles keep deletion protection, retained KMS/S3 resources, S3
versioning/Object Lock, and AWS Backup.
Restore tests remain operator-led: perform and record an RDS point-in-time
restore and S3 restore before launch and quarterly. Application rollback uses
the previous image digest; database migrations use expand/contract and forward
repair. Never automatically restore a database during application rollback.
