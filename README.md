# Aurinara Platform Control Plane

Production AWS topology, shared-foundation ownership, and release instructions
are documented in [`docs/aws-deployment.md`](docs/aws-deployment.md).

Independent shared service for Aurinara identity mapping, canonical study identity,
study/site-scoped authorization decisions, and immutable cross-product objects.
Product databases never import this service's ORM models and never receive object-store
credentials or keys.

## Central Repository

Platform is the system of record for the shared Repository. It stores immutable,
checksum-addressed bytes separately from logical files and folders. Logical files
have append-only versions, product provenance, retention metadata, and an explicit
study/site permission envelope.

The Repository API is rooted at `/v1/studies/{study_id}/repository`. A caller must
hold `platform.repository.read` or `platform.repository.write` and the permission
declared by the source product. The Platform console owns upload, folders, rename,
archive/restore, version history, and download. Aurinara and EDC call the same API
with the signed-in user's token and expose only their explicit binding action.

Published entries are organized consistently as
`Study / Module / Solution / Purpose / File`. Platform creates these structural
folders idempotently from declared publication metadata. The Solution key contains
the stable owning aggregate ID, so two workbenches never share a folder; its label
uses the product-supplied code and name. For example, an Aurinara source appears
under `Study / Raw / RAW-01 · Primary Raw / Source Files`; its original access
envelope and provenance remain attached to the file entry.

Configure `OIDC_USER_AUDIENCES` with every frontend audience and `CORS_ORIGINS`
with the three exact frontend origins. Product service credentials are limited to
object publication and reference management.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
docker compose up -d
alembic upgrade head
python scripts/bootstrap_service_accounts.py --aurinara-client-id <client-id> --edc-client-id <client-id>
uvicorn aurinara_platform.main:app --reload --port 8002
```

The AWS migration task runs the service-account bootstrap automatically with the
Cognito client IDs created by the environment stack. The command above remains
available for local development and is safe to rerun.

Confirmed Cognito users are registered automatically with one non-assignable,
create-only Platform role and no study/site or product assignments. Cognito's
post-confirmation trigger writes a minimal,
KMS-encrypted SQS message, and the supervised `identity-registration` worker creates
the idempotent Platform identity and normal user event. Every AWS migration task also
reconciles all confirmed, enabled users already present in the environment's user pool,
so users created before the trigger was installed are backfilled safely. Registration
does not expose existing studies or grant product-wide access. Creating a canonical
study atomically assigns its creator the reviewed study-owner roles synchronized by
Aurinara and EDC; other access remains managed through **Product access** and
**Memberships**.

After the first development administrator has signed in, grant that administrator
the local product roles used to exercise the cross-product runtime:

```powershell
python scripts/bootstrap_local_product_access.py
```

This command is rejected outside `ENVIRONMENT=development`. Production assignments must
continue to come from reviewed migration snapshots or explicit administrative actions.

The API is under `/v1`; OpenAPI is at `/v1/openapi.json`. Local browser authentication uses
the production user pool with separate localhost-only development app clients, so it follows
the same authorization-code + PKCE flow as production without granting the development
clients a production callback. After the production Platform stack exposes those clients,
configure all three ignored local environments from its public outputs:

```powershell
python scripts/configure_local_managed_login.py --write
```

Run the command without `--write` to preview the files and keys it will update. It preserves
unrelated dotenv values and does not read or print service-client secrets. Production refuses
default object credentials or a disabled malware scanner.

The local Compose stack exposes PostgreSQL on `55433`, MinIO on `9200` (console `9201`),
and LocalStack EventBridge/SQS on `4567`. It creates the versioned
`aurinara-shared-objects` bucket, the `aurinara-platform` event bus, and one queue for each
product. The local MinIO instance has no KMS, so development uses
`OBJECT_STORE_SSE_ALGORITHM=none`; that value is rejected when `ENVIRONMENT=production`.
Configure `AES256` or `aws:kms` with a real S3/MinIO KMS deployment outside local
development.

## Administrative frontend

The independent React console lives in `frontend/` and uses Cognito managed login with
authorization code + PKCE. Cognito uses a public development app client without a client
secret and sends the ID token to the platform API. The console never receives service
credentials, object-store credentials, or storage keys.

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Vite serves the console at `http://localhost:5174` and proxies `/v1` and `/health` to the
control plane at `http://localhost:8002`. The Platform development app client registers
`http://localhost:5174/auth/callback` and uses its own audience. Grant administrative
permissions through reviewed roles.

For independent deployment, build `frontend/Dockerfile` with the `COGNITO_*` and optional
`OIDC_*` public build arguments and the control-plane URL as `PUBLIC_API_URL`. The resulting
unprivileged Nginx image listens on port `8080`, includes a `/healthz` probe, and routes
OIDC callbacks back to the SPA without caching the application shell.

Enable bucket versioning, access logging, private network policy, encryption, and Object
Lock in environment infrastructure. The local Compose stack enables versioning; production
can select `AES256` or `aws:kms` and optional governance/compliance retention settings.

## Bootstrap and migration

Export reviewed data from the product-specific migration tools and import it atomically:

```powershell
python scripts/import_snapshot.py .\migration\reviewed-control-plane-snapshot.json
```

The snapshot uses schema version `1.0.0` and contains users, studies, roles with explicit
namespaced permissions, and scoped assignments. Existing UUIDs are preserved. Any UUID,
OIDC subject, or study-code conflict aborts the transaction; the importer never guesses a
match. Re-running an identical snapshot is idempotent.

EDC registers its product-owned permission vocabulary through
`PUT /v1/products/aurinara-edc/permissions`. Platform owns role names, scopes, status, and
permission composition. Administrators configure those roles in **Roles & permissions**;
every change requires a reason, advances the policy version, and emits a role projection
event. System-scoped roles are assigned in Product Access, while study- and site-scoped
roles are assigned in Memberships. The former product role-sync endpoint now rejects
writes so a product deployment cannot overwrite centrally reviewed policy.
CTMS-created Sites are registered under the matching canonical Study through the EDC
service account. Memberships list those active Sites by name and persist their stable UUID.

Product API service accounts are normal registered OIDC identities. Grant them
`platform.authorization.decide` and only their product's `*.object.upload`,
`*.object.download`, and `*.object.reference` permissions. The EDC service account also
requires `edc.permission.sync`, `edc.role.sync`, and `edc.site.sync`. End-user tokens are
never exchanged for bucket credentials.

Run `python scripts/publish_outbox.py --watch` from a supervised worker. The local
Compose stack provides the equivalent `outbox-publisher` service, so
`docker compose up -d outbox-publisher` keeps delivery active across terminal restarts.
Delivery is at least once;
consumers persist event IDs and aggregate sequences before acknowledging their queue
messages. Membership and account events also invalidate product authorization caches.

For a local SQS-compatible queue, consume one batch or keep the identity-registration
worker running with:

```powershell
python scripts/sync_cognito_users.py
python scripts/sync_cognito_users.py --watch
```

## Runtime connections

Create separate confidential Cognito app clients for `aurinara` and `aurinara-edc` with the
client-credentials flow and only the `https://aurinara-platform.local/service` custom scope.
Put each secret only in that product's ignored `.env`; never expose it to either frontend.
The platform accepts service access tokens only when their `client_id` is listed in
`OIDC_SERVICE_CLIENT_IDS` and the custom scope is present.

For this local stack, configure each product with:

```dotenv
PLATFORM_CONTROL_PLANE_URL=http://127.0.0.1:8002
PLATFORM_AUTHORIZATION_MODE=shadow
PLATFORM_SERVICE_TOKEN_URL=https://<cognito-domain>/oauth2/token
PLATFORM_SERVICE_SCOPE=https://aurinara-platform.local/service
PLATFORM_SERVICE_RESOURCE=https://aurinara-platform.local
PLATFORM_EVENT_REGION=us-east-1
PLATFORM_EVENT_ENDPOINT_URL=http://127.0.0.1:4567
```

Use `http://127.0.0.1:4567/000000000000/aurinara-platform-events` for Aurinara's queue
and `http://127.0.0.1:4567/000000000000/aurinara-edc-platform-events` for EDC's queue.
After applying each product's projection migration, run its projection worker in watch mode:

```powershell
# aurinara/
.venv\Scripts\python.exe scripts\platform_projection_worker.py --watch

# aurinara-edc/backend/
.venv\Scripts\python.exe -m app.workers.platform_projection_worker --watch
```

Keep authorization in `shadow` until decision mismatches have been reconciled. LocalStack,
the default MinIO credentials, and the development malware-scanner bypass are local-only;
production requires managed credentials, private networking, encryption, retention policy,
and a required malware scanner.

## Rollout modes

Integrating products support `disabled`, `shadow`, and `enforced` authorization modes.
Start with `shadow`, reconcile every local/central mismatch, and move permission families
to `enforced` only after the import report is clean. No legacy records or objects should be
deleted during the rollback window.
