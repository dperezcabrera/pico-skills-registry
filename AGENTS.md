# AGENTS.md

Conventions for working on pico-skills-registry (application, not a pico module).

## What this is

Self-hostable, authenticated skills registry for coding agents. Application layers:

- `contract.py` - the skill contract: payload validation, canonical content hash, frontmatter parser. Storage-independent.
- `store.py` - the storage PORT (`SkillStore` protocol) and the `db` driver (pico-sqlalchemy). New drivers must pass the same behavior covered by tests/test_registry.py; `git` driver is planned.
- `service.py` - authorization (writer_roles, group membership, author-or-admin), FTS5 search snapshot, seed import, boot warmup (fail-fast) and the health indicator.
- `app.py` - HTTP surface only; controllers delegate to the service and map domain errors to status codes.
- `catalog.py` - directory validator used by catalog repositories in CI and by the seed import.

## Invariants (do not break)

- No anonymous path: every skill endpoint requires a JWT. A skill outside the caller's groups is a 404, never a 403.
- Versions are immutable: updates insert, never overwrite; the server enforces the version bump.
- Every mutation writes an audit row (subject, roles, action, version, sha256).
- Credential-looking resources (`.env`, `id_rsa`, `.pem`, `.key`, `.p12`) are rejected at every entry point (API and directory import).
- Boot is fail-fast: unknown backend, unreachable database or invalid seed kill the container at startup.
- Product endpoints live under `/api/v1/...`; `/actuator/*` is the operational plane and is not versioned.

## Working on it

```bash
pip install -e ".[dev]"
pytest -q          # hermetic suite (pico-testing: pico_module is set in pyproject)
ruff check . && ruff format --check .
./smoke.sh         # docker compose end to end, exercises the write cycle
```

Tests boot the real container with `make_container`/`make_client`; the only stub is the JWKS fetch (issuer and API are the same in-process app). Commit messages: one line, English, imperative. Releases: tag `vX.Y.Z` + GitHub release publishes the image to GHCR; update CHANGELOG.md in the same commit.
