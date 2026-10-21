# Aurinara deployment topology

Use this as a discovery baseline, not a substitute for current repository files or live AWS and GitHub state.

## Repository and environment map

| Product | Expected GitHub repository | Default branch | Production stack | Production URL | Health endpoint |
| --- | --- | --- | --- | --- | --- |
| Platform | `qingyuan1017/aurinara-platform` | `main` | `aurinara-platform-production` | `https://platform.aurinara.com` | `/health/live` |
| Aurinara | `qingyuan1017/aurinara-program` | `master` | `aurinara-production` | `https://nara.aurinara.com` | `/health/ready` |
| EDC | `qingyuan1017/aurinara-edc` | `main` | `aurinara-edc-production` | `https://auri.aurinara.com` | `/api/v1/health/ready` |

Staging stack names add `-staging` in place of `-production`. Staging URLs are `platform-staging.aurinara.com`, `staging.aurinara.com`, and `edc-staging.aurinara.com` unless the current workflow's `BASE_DOMAIN` overrides them.

## Ownership

Platform owns the shared environment foundation:

- GitHub OIDC provider and deployment trust foundation.
- VPC, application/runner/data subnets, NAT, and shared internal ALB in the budget profile.
- Shared WAF and CloudFront VPC-origin integration.
- Cognito user pool, product SPA clients, service clients, token endpoints, issuer, and JWKS metadata.
- Platform EventBridge bus, shared identity key, registration flow, and suite CloudTrail.

The persistent production user pool also owns three localhost-only development
SPA clients. After those clients exist, use
`scripts/configure_local_managed_login.py --write` from the Platform repository
to configure the ignored local environments for production-equivalent PKCE login.
The development audiences are intentionally not accepted by production product
APIs.

Aurinara and EDC are consumers. They resolve matching Platform stack outputs during deployment and retain their own security groups, databases, buckets, queues, ECS tasks, CloudFront distributions, backups, and alarms.

The required dependency order is:

```text
aurinara-platform-<environment>
  -> aurinara-<environment>
  -> aurinara-edc-<environment>
```

The three workflows do not encode a cross-repository dependency. Enforce this order operationally whenever shared Platform outputs or contracts change.

## Delivery mechanics

Each repository has an independent GitHub Actions workflow:

- Pushes to the default branch build and scan an immutable ECR image, skip staging, and enter the protected production job.
- Manual `workflow_dispatch` with `deploy_staging=true` deploys staging before the production gate.
- The production environment may require a human reviewer in GitHub.
- Concurrency has `cancel-in-progress: false`, so a later run queues rather than replacing an active release.

Production concurrency groups are:

- `aurinara-platform-production-delivery`
- `aurinara-production-delivery`
- `aurinara-edc-production-delivery`

## Deployment profiles

`budget` is the workflow default. It uses one Platform-owned ALB/WAF, one NAT gateway, separate Single-AZ databases, one always-on API task per product, and scheduled drain-to-empty Fargate Spot workers.

`standard` restores dedicated consumer edge/network resources, higher database availability, continuously running workers, and broader redundancy. A profile change can replace or restart infrastructure and is a maintenance-window operation.

## Product-specific migration commands

Confirm these against the current CloudFormation compute template before relying on them:

- Platform: Alembic upgrade, service-account bootstrap, default-access bootstrap, and confirmed Cognito-user backfill.
- Aurinara: Alembic upgrade, catalog seed, CDISC registry publication, and Platform access synchronization.
- EDC: Alembic upgrade and built-in role seeding.

## Frontend publication

After backend rollout, the workflow builds the SPA using environment outputs, synchronizes it to the environment's S3 frontend bucket, invalidates CloudFront, and performs a public health check. A backend deployment can therefore be healthy even if a later frontend publication step fails; report these states separately.
