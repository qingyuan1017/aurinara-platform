# Release scenarios

## Common preflight

Before presenting an executable deployment action:

1. Discover the intended repositories and confirm their remotes.
2. Fetch remote refs and inspect branch, status, divergence, and the candidate commit without discarding local changes.
3. Read the current deployment workflow, deploy script, CloudFormation parameters, and applicable runbook.
4. Inspect the latest relevant GitHub runs and any active concurrency occupant.
5. Confirm the matching Platform stack is healthy before a consumer release.
6. Identify environment, deployment profile, change type, migration presence, and expected public verification.
7. Present the exact trigger and impact for confirmation.

Prefer GitHub Actions for all routine releases. Do not run a production deploy script locally merely because AWS credentials are available.

## Routine single-repository production release

Use when the change is isolated and does not alter shared Platform outputs or contracts.

1. Verify the intended commit is on the repository's default branch.
2. If the user has only asked for deployment, do not invent or push code changes.
3. Present the repository, commit, production environment, staging choice, and expected migration for confirmation.
4. Trigger or follow the workflow after confirmation.
5. Ask the user to approve the GitHub production environment when the job reaches that gate.
6. Monitor build, migration, CloudFormation, ECS, frontend publication, and health verification to completion.

## Full-suite release

Use when Platform networking, identity, EventBridge, shared edge, access contracts, or deployment profile changed, or when the user explicitly requests all three systems.

1. Prepare one release manifest listing each repository and exact commit.
2. Obtain confirmation for the complete ordered release. The confirmation authorizes only the listed runs.
3. Deploy and fully verify Platform.
4. Deploy and fully verify Aurinara.
5. Deploy and fully verify EDC.
6. Stop at the first failure. Do not start the next consumer while an earlier dependency is unhealthy.
7. Perform suite-level login, product-access, and projection checks after all three complete.

## Staging rehearsal

Use for significant migrations, authentication or authorization changes, shared-infrastructure changes, profile changes, or when the user explicitly requests staging.

1. Dispatch the relevant workflow manually with `deploy_staging=true` after confirmation.
2. Verify staging migration, health, login, product authorization, and the changed workflow.
3. Do not treat staging success as production approval. The user must separately approve the GitHub production environment gate.
4. The workflow reuses the same built digest for its staging and production jobs.

For a suite-wide rehearsal, use Platform, Aurinara, then EDC for staging and repeat that order for production.

## Hotfix without staging

Normal default-branch pushes already skip staging. Use this only when the user explicitly accepts the production-first path.

Verify the patch is narrow, tests passed, the schema change is backward-compatible, and the previous digest is known. Approval of the code change is not automatically approval to trigger production.

## First deployment or bootstrap refresh

Bootstrap is separate from an ordinary release. Platform bootstrap owns the account GitHub OIDC provider; consumer bootstraps consume its provider ARN.

Use this order:

1. Platform bootstrap.
2. Aurinara bootstrap.
3. EDC bootstrap.
4. Configure reviewed GitHub environment variables and secrets from bootstrap outputs.
5. Deploy Platform, Aurinara, and EDC environment stacks in that order.

Before any bootstrap mutation, show the exact stack, GitHub owner/repository/branch, IAM capabilities, and whether an existing bootstrap stack will be updated. Never print secret values.

## Deployment-profile change

Changing between `budget` and `standard` is not a routine release. Require an explicit maintenance-window confirmation and read the current `docs/budget-deployment-profile.md`.

Confirm database replacement/restart risk, snapshots, expected downtime, network and edge replacements, retained resources, and the ordered suite cutover. Phase one may drain services before migration. Verify Platform shared outputs before starting either consumer.

## Staging removal

Use the committed **Remove staging environment** workflow after production verification. Removal is destructive and requires separate confirmation naming every stack.

Remove in this order:

1. EDC staging.
2. Aurinara staging.
3. Platform staging.

Explain that retained buckets, snapshots, backup recovery points, KMS-protected data, or log groups may remain. Do not manually delete retained resources unless separately authorized.

## Post-release suite verification

Select checks proportional to the change:

- Platform health, Cognito authorization-code login, logout, user registration/backfill, and expected product assignments.
- Aurinara health, Platform authorization, projection consumption, and one representative queued build or execution when relevant.
- EDC health, Platform authorization, projection consumption, and one representative export when relevant.
- CloudFront serves the new frontend without a local API origin.
- CloudFormation parameters and ECS task definitions reference the expected immutable digest.
