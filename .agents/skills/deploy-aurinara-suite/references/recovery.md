# Deployment diagnosis and recovery

Collect read-only evidence before proposing a mutation. Keep request IDs, workflow run IDs, stack events, ECS task IDs, and image digests available, but do not expose secrets or clinical payloads.

## Queued or waiting for approval

1. Inspect all jobs in the workflow run and identify the exact pending job.
2. Check whether an earlier run occupies the same repository concurrency group.
3. Distinguish `queued`, `in_progress`, and GitHub environment `waiting` states.
4. Do not create a replacement run while an equivalent run is active.
5. If the job is waiting on the protected `production` environment, ask the user to approve it in GitHub.
6. Cancelling an older run or triggering a replacement requires explicit confirmation naming both runs.

## Build or vulnerability-scan failure

No production mutation has occurred. Inspect the failed build step and logs, identify whether the failure is compilation, packaging, ECR, SBOM, or Trivy policy, and fix the source or dependency. Do not bypass a high/critical vulnerability gate merely to deploy.

## Snapshot failure

The infrastructure update has normally not started. Diagnose RDS identifier, snapshot name collision, permissions, quota, or database state. Do not skip the snapshot for production without a separately reviewed decision.

## CloudFormation failure

1. Read root-stack events, then events for the failing nested stack.
2. Record the first causal failure rather than only the final rollback message.
3. Check current stack status before retrying; wait for rollback or cleanup to finish.
4. Inspect parameter values without printing secrets.
5. Retry only after the cause is corrected and the user confirms the specific retry.

For a stack stuck in an in-progress state, inspect events and AWS service state. Do not update, continue rollback, cancel, or delete it without explicit confirmation.

## Migration failure

The deployment must stop before phase two. Capture the migration task exit code and sanitized CloudWatch logs. Determine whether the failure is Alembic, bootstrap/seed, identity backfill, or a dependency/network problem.

During an ordinary same-profile release, the existing API image should remain active. During a first deployment or profile conversion, services may remain at zero. Never activate the new application image after a failed migration, and never edit the production database manually without separate authorization.

## ECS rollout failure

Inspect:

- service deployments and rollout-state reason;
- desired, pending, and running counts;
- stopped-task reasons and container exit codes;
- target-group health;
- task-definition image digest and environment wiring;
- sanitized application startup logs;
- readiness endpoint behavior.

CloudFormation and the ECS circuit breaker should preserve or restore the previous healthy task where possible. Do not force a service to a different task definition until the user confirms the recovery action.

## Frontend publication or verification failure

First determine whether the backend is already live. Inspect frontend build output, S3 synchronization, CloudFront invalidation, public asset response, and browser network calls. Verify production assets do not contain a loopback API URL.

Rerunning a whole deployment may repeat migrations and snapshots. Prefer a narrowly supported frontend republish only when the current repository workflow or script provides it and the user confirms the action.

## Authentication or authorization regression

Correlate the browser callback, token exchange, session cookie or bearer token, `/auth/me`, Platform user projection, and role assignments. Confirm the product uses the Platform-owned Cognito pool and the correct product SPA client. Distinguish authentication failure from missing product/study authorization.

Use request and correlation IDs to search sanitized logs. Never copy tokens into commands, chat, or logs.

## Application rollback

Rollback is an explicit recovery operation, not an automatic reaction to a failed health check.

1. Identify the last successful workflow run, commit, ECR digest, ECS task definition, and compatible CloudFormation template revision.
2. Confirm that the current expanded database schema remains compatible with the previous application image.
3. Present the exact prior digest, affected repositories, environment, and expected impact for confirmation.
4. Redeploy through the repository's supported CloudFormation path. Preserve the migration/application-image phase rules even if no new migration is expected.
5. Verify ECS stabilization, frontend compatibility, and public health.

Never automatically downgrade Alembic or restore RDS. If the prior application cannot operate against the current schema, prepare a reviewed forward repair instead.

## Database recovery

Database restore is separate from application rollback and always requires explicit target-specific authorization. Preserve the source instance and snapshot, restore to a new isolated instance, perform integrity checks, and plan a reviewed cutover. Never point production services at an unverified restored database.

## Retry stopping conditions

Do not loop indefinitely. Stop and request direction when:

- the same corrected action fails again for the same unexplained cause;
- recovery requires changing data, secrets, IAM trust, DNS ownership, or retained resources beyond the approved scope;
- the intended commit or digest cannot be established;
- Platform dependencies are unhealthy;
- a migration is not demonstrably backward-compatible;
- live state conflicts with the committed deployment contract.
