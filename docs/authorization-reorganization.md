# Authorization model review and reorganization plan

> Implementation decision: products register permissions only; Platform owns
> all roles. Permission codes use `<product>.<module>.<action>`. Platform ships
> System Administrator, Study Administrator, and Study Viewer policies, and
> assigns Study Administrator to the study creator. Legacy role and permission
> data is not migrated.

Status: role/permission ownership implemented; scope-resolution follow-up remains
Reviewed: 2026-10-19
Repositories: `aurinara-platform`, `aurinara`, `aurinara-edc`

## Implementation status

Implemented in this change:

- Platform owns stable built-in and custom role definitions; product role-sync
  APIs and clients are removed.
- Aurinara and EDC register canonical permission catalogs only.
- System Administrator dynamically covers every active registered permission.
- Study Administrator dynamically covers every active study/site permission;
  Study Viewer covers registered read permissions.
- Platform provides module-oriented Aurinara and EDC functional roles and emits
  updated role projections whenever a product permission catalog changes.
- Platform validates permission/product consistency and active site ownership.
- EDC projects shared Platform roles using only its own `edc.*`, `ctms.*`, and
  `pv.*` permissions, and its frontend evaluates study/site-scoped grants.

Still separate from this role/catalog reorganization: EDC object-addressed
routes need authoritative object-to-study/site scope resolution. The detailed
inventory below is retained as the follow-up security work item.

The “Current model” and “Findings” sections below record the pre-change
investigation; the implementation status above is authoritative where they
differ.

## Executive summary

The intended architecture is sound: Platform is the policy decision point, each
product declares its capabilities, assignments are scoped to system, study, or
site, and product APIs remain the enforcement points. The implementation is in
a transitional state, however, and currently has multiple overlapping sources
of truth.

The highest-priority issue is not role naming. It is scope resolution. The
generic EDC guard only extracts `study_id` and `site_id` from path and query
parameters. Many Designer, CTMS, and PV routes identify their target by another
object ID or carry the scope in the request body. With no claimed query scope, a
study- or site-scoped role is denied before the route can discover its scope.
More seriously, an undeclared `?study_id=` or `?site_id=` query parameter is
still read by the guard. A caller can therefore authorize against one scope
while the route loads or mutates an object from another scope unless the route
performs a second object-aware check. Static inventory found:

- 39 of 45 directly guarded Designer routes without a `study_id` argument;
- 41 of 47 guarded PV routes without a `study_id` argument;
- 79 of 202 guarded CTMS routes without a `study_id` argument.

Some routes already use the safer pattern: load the object, derive its study and
site, then call `PermissionService.require`. That should become the universal
pattern for object-addressed routes.

The second structural issue is policy ownership. Product code, Platform code,
deployment scripts, migrations, and documentation do not currently agree on
whether built-in roles are owned by the product or centrally editable in
Platform. The code makes synchronized roles product-managed and immutable in
Platform, while Platform documentation says Platform owns role composition.

No in-place permission renaming should begin until scope enforcement and policy
ownership are fixed. Existing codes are security contracts and should be
migrated through explicit aliases and dual-read verification, never by a broad
search-and-replace.

## Current model

### Aurinara Platform

Platform stores the canonical user, study, site, permission, role, role-to-
permission, and scoped assignment records. It evaluates authorization requests
against active users, active roles, active permissions, and assignment scope.
It publishes user, study, role, and membership changes through its outbox.

Platform currently owns seven `platform.*` study/membership/repository
permissions and two baseline roles:

- `Registered Study Creator`: non-assignable, system scope, create-only;
- `Platform Study Owner`: assignable, study scope, Platform study and membership
  management.

The initial administrator additionally receives a non-assignable Platform
Administrator role and the EDC System Administrator role.

### Aurinara

Aurinara declares nine broad `artifact.*` permissions and one built-in role,
`Aurinara Study Owner`, containing all nine. It has two authorization paths:

- legacy local `StudyMembership` records when Platform authorization is
  disabled;
- Platform decisions in shadow or enforced mode.

Legacy membership `role` and `scopes` fields do not provide action-level
enforcement: any local member satisfies any requested Aurinara action. Platform
enforcement is consequently much more granular than disabled-mode behavior.

### Aurinara EDC

EDC declares 94 permissions across five catalog modules:

| Module | Permission count |
| --- | ---: |
| shared | 16 |
| designer | 28 |
| operational EDC | 30 |
| CTMS | 10 |
| PV | 10 |

EDC defines 19 built-in roles but synchronizes only six to Platform:

- System Administrator;
- Study Administrator;
- Designer_Role;
- Operational_Role;
- CTMS_Operations_User;
- Safety_Manager.

The remaining specialized roles exist only in the local catalog unless an
administrator recreates equivalent roles centrally. A new study creator is
assigned the five study-scoped roles above, rather than one explicit owner
bundle.

## Findings

### P0: Object-addressed route guards cannot resolve study/site scope

`require_permission` extracts only `study_id` and `site_id` from path or query
parameters. It cannot infer scope from `version_id`, `form_id`, `case_id`,
`activity_id`, or a request body. Examples include Designer arm routes and most
PV case lifecycle routes.

Effects:

- study/site users are denied valid work because their assignment cannot cover
  a request with no resolved study;
- callers can spoof an authorized query scope on an object-ID or body-scoped
  route; where no later object-aware comparison exists, this can expose or
  mutate a different study's data;
- system administrators pass, so system-role tests can conceal the defect;
- authorization behavior depends on URL shape rather than the target object's
  authoritative ownership;
- several PV services rely on the route guard and do not repeat an object-aware
  permission check after loading the record.

Target rule: a protected operation must authorize the authoritative object's
resolved `(study_id, site_id)`, not untrusted request placement. Collection and
create operations must authorize the explicit parent scope after request
validation. Object operations must load a minimal ownership projection first,
authorize it, and only then load or mutate sensitive content.

### P0: Inactive canonical sites do not revoke site-scoped decisions

Platform verifies that a referenced study exists and is active, but it does not
load or validate `resource.site_id` during a decision. Site synchronization can
mark a site inactive while its existing site-scoped assignments continue to
authorize requests.

Target rule: when a site is supplied, Platform must verify that the site exists,
is active, and belongs to the requested study before evaluating assignments.

### P1: Role ownership is contradictory

Platform exposes central role create/update operations, but synchronized roles
with `source_role_id` are rejected as source-managed. EDC and Aurinara startup
scripts synchronize built-in role composition from product code. Platform
documentation, meanwhile, states that Platform owns role names, scopes, status,
and permission composition.

Implemented ownership model: products publish permissions only. Platform owns
all built-in and custom role composition, identified by stable role keys and an
explicit `built_in` flag. Product role synchronization has been removed.

### P1: The EDC local and central catalogs are only partially aligned

- 19 local built-in roles exist, but only six are registered centrally.
- The required Sponsor Viewer role is present in requirements and design but
  absent from `ROLE_DEFINITIONS`.
- `role.update` is declared but has no runtime use.
- `designer.odm.import` and `designer.odm.export` are absent from the frontend
  permission constants.
- `PlatformRoleMapping` and its CLI remain, but runtime projection now uses
  stable identical role IDs and never reads this mapping.
- role names mix display labels and machine-style identifiers, such as
  `Designer_Role`, `CTMS_Operations_User`, and `Safety_Manager`.

Target rule: every supported built-in role has a stable machine key, stable UUID,
human display name, owner, scope, catalog version, and exact permission set in
one versioned manifest. Backend seeding, Platform synchronization, frontend
labels, documentation, and tests should be generated or validated from that
manifest.

### P1: Permission namespace conventions are inconsistent

Current central codes use several incompatible patterns:

- `platform.study.read` for Platform;
- `artifact.study.read` for Aurinara;
- `edc.study.read` and `edc.designer.form.read` for EDC;
- `ctms.operational_data.read` for CTMS;
- `edc.safety_case.read` for PV after adapter prefixing.

CTMS routes additionally mix stored underscore codes with hyphenated aliases.
PV is classified as module `pv`, but its central wire codes are prefixed `edc`.

Because legacy authorization data does not need to be retained for this
release, product catalogs now emit canonical codes directly and do not carry a
dual-read alias migration.

Implemented central pattern:

```text
platform.<resource>.<verb>
aurinara.<resource>.<verb>
edc.<module-or-resource>.<verb>
ctms.<resource>.<verb>
pv.<resource>.<verb>
```

Designer capabilities consistently use `edc.designer.*`; the namespace remains
product-qualified while preserving the separation-of-duties boundary.

### P1: Permission retirement does not fully project

Platform permission synchronization marks omitted permissions inactive and
bumps the policy version. It does not emit updated role projections for roles
that still contain the retired permission. EDC's local projected roles and the
frontend `/auth/me` response can therefore retain a permission that Platform
now denies.

Target rule: retiring a permission must either:

- atomically remove it from affected roles and emit role changes; or
- publish a permission-status event consumed by every product and exclude
  inactive permissions from local grants and UI capability responses.

### P1: EDC enforced-mode UI scope is flattened

The backend returns role assignments with their study/site scope. The frontend
flattens all permission codes into one list and ignores the `studyId` and
`siteId` arguments to `hasPermission`. This can show a route or action for study
B merely because the user has that permission in study A. The API remains the
security authority, but the UI is inconsistent and produces avoidable denials.

Target rule: frontend checks operate on scoped grants, not a flat union. A
system grant covers all scopes, a study grant covers its study and sites, and a
site grant covers only its exact study/site.

### P1: Deployment authorization modes disagree

The deployed EDC stack hard-codes enforced mode. Aurinara deployment defaults
to shadow mode, including production, unless a repository variable overrides
it. Shared Platform documentation still describes shadow as the rollout state.

Target rule: authorization mode is an environment-level release decision with
one recorded value for both products. Production should not silently mix
central enforcement in one product with local enforcement in another.

### P2: Product validation is missing from Platform decisions

Authorization requests include `resource.product`, but decision evaluation
selects only by action code and does not verify that the permission belongs to
the declared product. Global uniqueness of permission codes limits practical
cross-product escalation, but the product field is not currently an enforced
security boundary.

Target rule: reject or deny a decision when the active permission's product does
not equal `resource.product`. Service identities should also be restricted to
delegating decisions for their own registered product.

### P2: Scope metadata and role composition need stronger invariants

`allowed_scope_levels` is derived heuristically in EDC. Several shared codes
therefore allow site scope even when their semantics appear study- or
system-level. Platform validates scope compatibility when roles are created,
but database constraints do not ensure assignment shape matches role scope.
Nullable columns also make uniqueness for system/study assignments dependent on
service behavior rather than a fully effective PostgreSQL unique key.

Target rule:

- declare allowed scopes explicitly per permission;
- constrain assignment shape (`system`: no study/site, `study`: study only,
  `site`: study and site);
- use null-safe unique indexes for assignments;
- validate site membership and status at decision time;
- reject multi-module study/site roles unless explicitly designated as a
  reviewed bundle.

### P2: Tests are environment-sensitive

Focused Platform and Aurinara authorization suites pass. EDC's focused suite
passes 99 tests when `PLATFORM_AUTHORIZATION_MODE=disabled`, but a local `.env`
with enforced mode causes 32 core permission-service tests to call the live
gateway and fail on local stubs. Unit tests must not inherit deployment mode or
credentials from a developer dotenv file.

Target rule: test settings explicitly disable or inject the Platform gateway by
default. Separate adapter tests explicitly opt into shadow/enforced behavior.

## Proposed target model

### Authority

| Concern | Authority | Product responsibility |
| --- | --- | --- |
| Identity and account status | Platform | Validate token; resolve projected user |
| Canonical study/site status | Platform | Project identity; own product-specific lifecycle |
| Permission definition | Product manifest, registered in Platform | Enforce only declared capabilities |
| Built-in role template | Product manifest, immutable by Platform | Version and test exact composition |
| Custom role | Platform | Consume projected definition |
| Assignment | Platform | Project for UI/list filtering only |
| Decision | Platform | Enforce centrally in enforced mode |
| Object-to-scope resolution | Owning product | Resolve before requesting decision |
| UI affordance | Product frontend | Evaluate projected scoped grants |

### Stable role identity

Separate identity from presentation:

```text
role_id          stable UUID
role_key         stable machine key, e.g. pv.safety_manager
display_name     editable human label
product          owning product
module           owning module
scope_level      system | study | site
managed_by       product | platform
catalog_version  version that defined built-in composition
status           active | inactive
permissions      exact canonical permission codes
```

Code must never branch on `display_name`. Existing notification and CRF access
features that intentionally address job roles should store `role_id` or
`role_key`, not names.

### Role bundles

Represent the initial study owner grant as a reviewed bundle rather than a list
of names embedded in Platform code:

```text
study-owner
  platform.study_owner
  aurinara.study_owner
  designer.study_designer
  edc.study_operator
  ctms.study_operator
  pv.safety_manager
```

Bundles expand to ordinary role assignments and are not themselves evaluated
as permissions. This keeps module separation intact and makes owner policy
auditable and versioned.

Sponsor Viewer should likewise be a bundle of module-specific read-only roles,
not one cross-module role that bypasses the existing module-conflict rule.

## Migration sequence

### Phase 0: Freeze and observe

1. Export the current Platform permission, role, and assignment graph.
2. Add validation reporting for unknown codes, invalid assignment shapes,
   inactive-site assignments, duplicate null-scoped assignments, and local /
   central decision mismatches.
3. Make authorization unit tests hermetic.
4. Keep existing codes and IDs unchanged.

### Phase 1: Correct scope enforcement

1. Add reusable object-scope resolvers for Designer versions/forms/fields, EDC
   records, CTMS records, and PV records.
2. Replace path-only guards on object routes with load-scope-authorize handlers.
3. Authorize body-scoped creates after schema validation and before persistence.
4. Add study-role, site-role, cross-study denial, and cross-site denial tests for
   every route family.
5. Deny inactive or mismatched sites in Platform decisions.

### Phase 2: Establish one catalog contract

1. Introduce a versioned product access manifest with explicit permission and
   built-in role metadata.
2. Register every supported EDC built-in role, or explicitly retire it from the
   local catalog.
3. Add the missing Sponsor Viewer bundle and read-only component roles.
4. Add missing frontend Designer ODM permissions and remove or implement
   `role.update`.
5. Remove the unused Platform role mapping table and CLI after verifying no
   deployed consumer reads them.

### Phase 3: Make Platform ownership explicit

1. Add stable `role_key`, `managed_by`, and `catalog_version` fields.
2. Make product-owned templates immutable centrally; allow Platform-owned
   custom roles to be edited with reason and revision.
3. Replace name-based logic with stable role IDs/keys.
4. Publish permission retirement and affected role changes atomically.
5. Version the study-owner and viewer bundles.

### Phase 4: Normalize namespaces and UI

1. Define canonical central codes and explicit legacy aliases.
2. Dual-read old and new codes while emitting only canonical codes.
3. Migrate assignments/roles deterministically and compare decisions.
4. Change the EDC frontend to evaluate scoped grants.
5. Remove aliases only after telemetry shows no old-code requests.

### Phase 5: Unified enforcement cutover

1. Run both products in shadow against the same policy snapshot.
2. Reconcile all decision mismatches.
3. Set one environment-level mode for Aurinara and EDC.
4. Move both to enforced together.
5. Retain projection and local-data rollback paths for a bounded period, without
   permitting local policy to override central denials.

## Required verification

- Platform decision tests for product mismatch, inactive sites, site/study
  mismatch, inactive users/roles/permissions, and revocation.
- Contract tests generated from every product manifest.
- A test proving each built-in role contains exactly its reviewed permissions.
- A route-family matrix proving system, study, and site behavior for positive,
  wrong-study, wrong-site, inactive-site, and removed-assignment cases.
- Frontend tests proving a permission in study A does not show an action in
  study B.
- Projection tests for out-of-order role/membership events and permission
  retirement.
- A deployment check that Aurinara and EDC use the same authorization mode and
  compatible catalog versions.

## Immediate implementation order

The safest first implementation slice is:

1. make EDC authorization tests independent of `.env`;
2. add Platform inactive-site and product-boundary checks;
3. introduce object-aware scope resolution and migrate Designer routes first;
4. migrate PV, then CTMS, in route-family batches;
5. only then consolidate manifests, roles, names, and permission aliases.

This order fixes correctness before presentation and avoids making a tidy
catalog sit on top of incorrect scope enforcement.
