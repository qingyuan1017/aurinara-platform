---
name: deploy-aurinara-suite
description: Plan, trigger, monitor, verify, troubleshoot, or roll back AWS deployments for Aurinara Platform, Aurinara, and Aurinara EDC. Use for staging or production releases, queued or failed GitHub Actions runs, CloudFormation or ECS rollout checks, deployment-profile changes, bootstrap work, and staging removal; do not use for ordinary application development unrelated to delivery.
---

# Deploy the Aurinara suite

Operate the three repositories as one suite while preserving their independent release pipelines. Prefer the committed GitHub Actions workflows for releases; use direct AWS commands for read-only evidence or an explicitly authorized recovery operation.

## Establish current context

1. Read the applicable `AGENTS.md` in every repository that will be changed.
2. Discover repository locations and remotes instead of assuming sibling paths. The expected remotes and production topology are in [references/topology.md](references/topology.md).
3. Read the target repository's current `.github/workflows/deploy.yml`, `scripts/aws-deploy-environment.sh`, and deployment documentation before planning or acting. Treat those files and live GitHub/AWS state as authoritative when they differ from a reference.
4. Identify the requested scenario, repositories, environment, deployment profile, commit or ref, and whether the user wants planning, execution, monitoring, diagnosis, or recovery.
5. Inspect the working tree and remote divergence. Never discard or overwrite local work as part of a deployment.

## Route the request

- For preflight, one-product releases, full-suite releases, staging rehearsals, hotfixes, bootstrap, profile changes, and staging removal, read [references/release-scenarios.md](references/release-scenarios.md).
- For queued, failed, stuck, unhealthy, or rollback requests, read [references/recovery.md](references/recovery.md).
- For a status explanation, use [references/topology.md](references/topology.md) plus live read-only evidence; do not trigger a workflow.

## Authorization boundary

Read-only inspection, comparison, planning, and monitoring do not require additional confirmation. Before an external mutation, present the exact action, repository or repositories, environment, commit or ref, and material impact, then obtain explicit human confirmation for that action.

External mutations include triggering, rerunning, cancelling, or dispatching a workflow; pushing a deployment commit; applying CloudFormation directly; changing ECS services; rolling back an image; deleting staging; changing DNS; restoring a database; or modifying retained resources.

Treat a response such as `approved` as authorization only for the immediately presented action and targets. It does not authorize later retries, a different run, additional repositories, rollback, deletion, or database recovery. Never approve or bypass a protected GitHub production environment gate for the user; stop and ask the user to approve it in GitHub.

Database restore, stack deletion, retained-data deletion, DNS cutover outside the committed workflow, and profile conversion each require separate target-specific authorization. Never expose tokens, secret values, authorization headers, database URLs, or clinical payloads in commands or output.

## Release invariants

- Deploy shared changes in this order: Platform, Aurinara, then EDC. Stop before consumers if Platform fails or its required outputs are unhealthy.
- Use the same digest built by a workflow across its staging and production jobs. Verify the live ECS task definition points to that digest.
- Preserve the two-phase rollout: register and run the new migration image while existing services retain the old application image, then activate the new application image only after migration exits successfully.
- Do not continue past a failed migration. Do not repair production data manually unless the user separately authorizes a reviewed repair.
- A normal push skips staging. Use a manual workflow dispatch with `deploy_staging=true` when the user requests or accepts a staging rehearsal.
- Do not deploy consumers against guessed or copied Platform identity/network values. Their scripts must resolve outputs from the matching Platform stack.
- Do not automatically restore a database during application rollback. Use a prior image digest with a backward-compatible schema or prepare a forward repair.
- Respect each workflow's concurrency group. Do not create duplicate runs merely because a deployment is queued or waiting for approval.

## Verification and handoff

Monitor through a terminal state. For successful production releases, verify:

1. GitHub build and production jobs completed successfully.
2. CloudFormation reached `UPDATE_COMPLETE` and termination protection remains enabled where configured.
3. The ECS service is stable, has the intended task definition and digest, and has no failed replacement tasks.
4. The public health endpoint succeeds.
5. The frontend publish and CloudFront invalidation completed.
6. Scenario-specific behavior works, such as Cognito login, Platform identity projection, an Aurinara queued job, or an EDC export.

Report the deployed commit and digest, GitHub run, stacks changed, verification evidence, and any remaining manual action. Do not claim success while a workflow, stack, or ECS rollout is still in progress.
