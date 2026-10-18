# Budget AWS deployment profile

## Purpose and boundary

The `budget` deployment profile is the supported low-traffic production topology
for the Platform, Aurinara, and Aurinara EDC. It targets approximately USD
250-300 per month before unusually high data transfer, storage growth, execution
bursts, or third-party charges. It preserves product isolation, encryption,
immutable artifacts, backups, authentication, and the existing public URLs. It
accepts reduced redundancy and several minutes of worker latency.

This is an operating target, not a fixed-price guarantee. AWS usage, taxes,
Cloudflare, domain registration, email, support plans, and optional scanners are
outside the estimate. Configure AWS Cost Anomaly Detection as a separate account
control if required.

## Topology

The Platform environment stack owns the shared VPC, CloudFront VPC origin, and
single internal ALB. Each product keeps its own CloudFront distribution and
hostname. CloudFront adds
an origin-only `X-Aurinara-Product` header; ALB listener rules route `aurinara`
and `edc`, while the default action remains Platform. The ALB security group
accepts CloudFront origin traffic and private calls from workloads in the same
VPC.

The budget profile uses:

- one NAT gateway in the first availability zone;
- one Platform-owned internal ALB and one Platform-owned WAF web ACL;
- ECR API, ECR Docker, and CloudWatch Logs interface endpoints in one AZ, plus
  the free S3 gateway endpoint;
- one small always-on API task per product;
- always-on Platform event publishing and Cognito identity registration;
- EventBridge-launched, drain-to-empty workers on Fargate Spot;
- three separate Single-AZ PostgreSQL instances, one for each product;
- 50 GiB initial database storage, 14-day database backups, and reduced hot-log
  retention;
- production deployment on every approved release, with staging deployed only
  when explicitly selected from the manual workflow.

The three databases are deliberately not consolidated into one physical RDS
instance. Separate credentials, lifecycle, backup, ownership, and failure scope
are platform boundaries. A shared database would save money but would make one
database incident or deployment affect all three products and would contradict
the current service-ownership contracts.

The standard profile remains available. It restores dedicated consumer ALBs,
PrivateLink, two NAT gateways, multi-AZ databases, always-on workers, broader
interface endpoints, longer retention, and API autoscaling.

## Expected latency and resilience

The Platform identity-registration and event-publisher workers remain
continuous. Aurinara build, execution-dispatch, and integration workers run
every five minutes; its Platform projection worker runs every minute. EDC export
runs every five minutes and its Platform projection worker runs every minute.
Each invocation drains currently eligible work and exits.

Scheduled workers use Fargate Spot because every queue is durable and terminal
transitions are idempotent. Spot interruption or temporary capacity shortage
delays work until the next schedule; it does not discard work. APIs and Platform
identity services do not use Spot.

Single-AZ databases and one NAT gateway remove availability-zone redundancy.
An AZ or database-instance event can cause downtime. Backups and retained data
remain recovery mechanisms, not high availability. Use the `standard` profile
when uptime requirements outweigh the budget ceiling.

## Cost envelope

The expected steady-state monthly envelope in `us-east-1` is:

| Component | Approximate monthly cost (USD) |
| --- | ---: |
| Always-on ECS API and Platform control workers | 65-80 |
| Scheduled Spot workers at configured cadence | 10-20 |
| Three `db.t4g.small` Single-AZ databases and initial storage | 80-100 |
| NAT, three interface endpoints, shared ALB, and WAF | 75-95 |
| CloudFront, S3, logs, secrets, queues, backups, and monitoring | 20-45 |
| **Expected total** | **240-310** |

The lower end assumes low traffic, short worker runs, modest storage, and normal
Spot availability. The CloudFormation budget is USD 300 and emits actual alerts
at 67, 84, and 100 percent plus a forecast alert at 80 percent when
`ALARM_EMAIL` is configured. A hard spend cap does not exist in AWS Budgets.

Review Cost Explorer after the first seven and thirty days. Execution tasks,
egress, retained snapshots, S3 versions, CloudWatch ingestion, and WAF requests
are the most likely sources of variance.

## Deployment sequence

The first transition changes networking and databases and must use a maintenance
window. Production workflows snapshot RDS before applying the change. Changing
Multi-AZ to Single-AZ or changing instance class can restart the database.

1. Redeploy each repository's bootstrap template once. This adds the
   `cloudformation:DeleteStack` permission used by the staging-removal workflow.
2. Merge and deploy `aurinara-platform` with `DEPLOYMENT_PROFILE=budget`.
3. Verify Platform health and the shared ALB outputs.
4. Merge and deploy `aurinara` with the same profile, then verify its health,
   authentication, projection, and one queued job.
5. Merge and deploy `aurinara-edc`, then verify its health, authentication,
   projection, and one export.
6. Use each repository's **Remove staging environment** workflow after the
   production checks succeed. Delete consumers before Platform.

The consumer deploy scripts read the shared ALB, WAF, VPC, identity, and event
outputs from the matching Platform stack. They disable deletion protection on
their old dedicated ALB during the first budget conversion so CloudFormation can
remove it. They do not delete retained buckets, snapshots, or backup recovery
points.

## Release and rollback

`DEPLOYMENT_PROFILE` defaults to `budget` in all three release workflows and can
be overridden by the matching GitHub environment variable.
Automatic pushes build and deploy production after the GitHub production
approval; they do not recreate staging. Use `workflow_dispatch` with
`deploy_staging=true` when a full staging rehearsal is needed.

Application rollback uses the prior image digest and a forward-compatible
database schema. To restore the higher-availability topology, set
`DEPLOYMENT_PROFILE=standard` and deploy Platform, Aurinara, and EDC in that
order. Database restoration is a separate reviewed recovery action and is never
an automatic rollback step.

## Acceptance checks

After each first budget deployment, confirm:

- all three CloudFront hostnames return their own health response;
- Cognito login and logout work and a newly confirmed user appears in Platform
  with no access assignments;
- Platform product assignments authorize the intended product only;
- Aurinara and EDC consume Platform projection events within the documented
  schedule interval;
- a build/execution and an EDC export reach a terminal state;
- no consumer dedicated ALB, consumer WAF, or PrivateLink endpoint remains in
  the budget stack;
- the AWS Budget subscriptions are confirmed and Cost Explorer shows the
  expected services after usage data settles.

