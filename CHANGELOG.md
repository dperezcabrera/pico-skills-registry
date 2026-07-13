# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-13

### Added
- RBAC: permissions assigned to groups (`can_write`) and users/agents assigned to groups, both admin-managed and audited (`/api/v1/groups`). A caller's effective groups are its token roles plus its registry memberships - one membership change takes effect immediately, without editing skills or reissuing tokens.
- `GET /api/v1/me`: resolved identity (subject, roles, effective groups, write permission).

## [0.3.1] - 2026-07-13

### Fixed
- **Fail-fast at boot**: the database connection and the seed catalog are now validated during container startup (warmup in the configure phase, loop-safe). An invalid seed used to boot a "healthy" instance that failed on the first request.
- `/actuator/health` now includes a `registry` indicator reflecting the loaded catalog (DOWN if not loaded, skill count when up).

## [0.3.0] - 2026-07-13

### Added
- Authenticated write API gated by assigned permissions (`registry.writer_roles`): create, update, deprecate, retire. A writer only publishes for groups it belongs to; updating or transitioning an existing skill is for its author or admin.
- Immutable version history (`GET /api/v1/skills/{name}/versions`): every update is a new version with a server-enforced bump; nothing is overwritten.
- Per-mutation audit trail with subject, roles, action, version and content hash (`GET /api/v1/audit`, admin only).
- Storage as a configurable port (`registry.backend`): `db` driver (SQLite on a volume by default, PostgreSQL via `DATABASE_URL`). A `git` driver is planned; unknown backends fail at boot.

### Changed
- The mounted catalog directory (`CATALOG_PATH`) is now a one-time SEED imported when the database is empty; the previous `POST /api/v1/catalog/reload` endpoint is gone.

## [0.2.0] - 2026-07-13

### Added
- Skill lifecycle in the catalog contract: mandatory `version: X.Y.Z`, `status: active|deprecated|retired` with `superseded_by`, status-aware search ranking.
- `POST /api/v1/catalog/reload` (admin): atomic, fail-safe catalog hot reload.

## [0.1.0] - 2026-07-13

### Added
- Authenticated skills registry: mounted catalog validated at boot (contract, credential-looking files rejected, sha256 per skill), FTS5/BM25 search filtered by `access.groups` against JWT roles, resource serving with path-traversal protection.
- Embedded pico-server-auth issuer; external issuer via `AUTH_ISSUER` (pico-client-auth validation).
- Docker image published to GHCR on release.
