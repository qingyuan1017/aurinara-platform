# Rich Repository architecture

Platform owns the Repository namespace, immutable object versions, authorization envelopes, format profiles, annotations, and processing jobs. Aurinara and EDC call the same Platform APIs with the signed-in user's token; they do not keep shadow file catalogs. Their product-specific bind actions continue to store only an exact Repository version ID and checksum.

## Version processing

Every new `repository_file_versions` row receives a pending `repository_version_profiles` row and an idempotent `analyze:<version-id>` job. `python -m aurinara_platform.repository_worker` leases work with PostgreSQL `FOR UPDATE SKIP LOCKED`, detects the format from bytes, and updates the profile. Failed jobs use bounded exponential retry and a terminal failure state.

CSV analysis records encoding, delimiter, and header detection and creates a hidden canonical Parquet object. CSV and Parquet preview queries run in a bounded DuckDB process with quoted identifiers, bound values, and limits on pages, filters, and sorts. Interactive preview is capped at 1 GiB. Conversions and annotated PDF exports create derived sibling files and retain `derived_from_version_id` provenance.

## PDF and Office collaboration

PDF annotations use normalized page coordinates and optimistic `If-Match` revisions. The current row is soft-deleted while each mutation is retained in immutable annotation history. Annotations belong to an exact file version and are visible in every application after normal Repository authorization succeeds.

ONLYOFFICE sessions are shared by node and current version. Platform signs the editor config and short-lived integration URLs, serves source bytes without exposing storage credentials, validates callback JWTs, document keys, download hosts, format signatures, size, and malware status, and creates a new immutable version only when the checksum changes. A live edit lease rejects competing external versions.

## Rollout and recovery

Deploy the database/API, start the Repository worker to backfill profiles, deploy ONLYOFFICE, then release the three clients. The following flags can disable features without deleting data:

- `REPOSITORY_RICH_PREVIEW_ENABLED`
- `REPOSITORY_CONVERSION_ENABLED`
- `REPOSITORY_PDF_ANNOTATIONS_ENABLED`
- `REPOSITORY_OFFICE_ENABLED`

The Office service is intentionally optional: when it is disabled or unavailable, Office files remain versioned and downloadable and session creation returns a clear `503`.

