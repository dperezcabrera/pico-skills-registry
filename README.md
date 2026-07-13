# pico-skills-registry

Self-hostable, authenticated skills registry for coding agents. One container serves a catalog of skills (instructions plus resources) with JWT authentication and group-based visibility - so agents discover exactly the skills they are allowed to use, through three progressive levels: search one-liners, full SKILL.md, individual resources.

The catalog is NOT part of this image: it lives in its own (typically private) repository and is mounted read-only at deploy time. The registry validates it on boot - contract, credential-looking files rejected, content hashes - and refuses to start on an invalid catalog.

## Run

```bash
docker run -p 8000:8000 \
  -e ADMIN_PASSWORD=change-me \
  -v /path/to/your-catalog:/catalog:ro -e CATALOG_PATH=/catalog \
  ghcr.io/dperezcabrera/pico-skills-registry:latest
```

Without a mounted catalog it serves the bundled example (three skills demonstrating the contract, a group-gated skill and a resource).

## API

All skill endpoints require a Bearer token; results are filtered by the token's role against each skill's `access.groups` (empty groups = any authenticated caller; `admin` sees everything). A skill outside your groups is a 404, not a 403 - callers cannot enumerate what they cannot use.

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/auth/login` | Obtain tokens (embedded issuer) |
| `GET /api/v1/skills?q=...` | Search: BM25 over name, description, triggers, tags |
| `GET /api/v1/skills/index` | Full catalog visible to the caller (one-liners plus hashes) |
| `GET /api/v1/skills/{name}` | Frontmatter, content hash and body |
| `GET /api/v1/skills/{name}/resources/{path}` | Individual resource |
| `GET /actuator/health` | Container healthcheck and probes |

Authentication is the pico auth pair: the embedded [pico-server-auth](https://github.com/dperezcabrera/pico-server-auth) issues tokens and [pico-client-auth](https://github.com/dperezcabrera/pico-client-auth) validates every request. To use an external issuer instead, set `AUTH_ISSUER` to its URL - validation only needs the issuer's JWKS endpoint, no code changes.

## The catalog contract

```
skills/<name>/SKILL.md      # YAML frontmatter + markdown body
skills/<name>/resources/*   # optional scripts and files
```

```yaml
---
name: deploy-service            # must match the directory
description: Deploy a service with health-gated rollout.
triggers: [deploy the service, como despliego]
tags: [ops, deploy]
access:
  groups: [ops]                 # omit for any authenticated caller
tools:
  - server: k8s                 # logical names, declared not enforced
    tools: [apply_manifest]
---
Body: loaded only when a caller fetches the skill explicitly.
```

Boot-time validation: frontmatter contract, name/directory match, at least one trigger, mandatory `version` (X.Y.Z), no credential-looking files (`.env`, `id_rsa`, `*.pem`, ...), resource size cap, sha256 per skill published in the index so consumers can verify integrity.

## Lifecycle: a real write API, permission-gated

Skills are dynamic: authenticated callers with assigned permissions create, update, deprecate and retire them through the API, and every mutation is audited.

| Endpoint | Rule |
|---|---|
| `POST /api/v1/skills` | Caller's role must be in `registry.writer_roles`; a writer only publishes for groups it belongs to (admin: any) |
| `PUT /api/v1/skills/{name}` | Author or admin; the server enforces a version bump - every update is a NEW immutable version, nothing is overwritten |
| `POST /api/v1/skills/{name}/deprecate` | Author or admin; optionally `{"superseded_by": "..."}`; still served, ranked last, flagged |
| `POST /api/v1/skills/{name}/retire` | Author or admin; hidden from all reads, history preserved |
| `GET /api/v1/skills/{name}/versions` | Immutable version history with hashes and authors |
| `GET /api/v1/audit` | Admin only: who did what, when, to which version |

Write payloads carry the same contract as SKILL.md frontmatter (name, version, description, triggers, tags, groups, tools, body, resources) and pass the same validation - credential-looking resource paths are rejected with a 422.

## Groups and permissions (RBAC)

Two independent, admin-managed assignments make permissions efficient: permissions go to groups, and users/agents go to groups. Changing one membership row takes effect immediately - no skill is edited, no token is reissued.

| Endpoint (admin) | Purpose |
|---|---|
| `PUT /api/v1/groups/{name}` | Create/update a group; `{"can_write": true}` grants write permission to its members |
| `PUT /api/v1/groups/{name}/members/{subject}` | Assign a user/agent to the group |
| `DELETE /api/v1/groups/{name}/members/{subject}` | Revoke the membership |
| `GET /api/v1/groups` | Groups with permissions and members |

A caller's effective groups are its token roles plus its registry memberships; `GET /api/v1/me` shows the resolved identity (subject, roles, effective groups, write permission). Skills keep declaring `access.groups`; visibility and write checks run against the effective set. Every group mutation lands in the audit trail.

## Storage backend

A conscious, configurable decision (`registry.backend`): the storage port (`SkillStore`) has one driver today, `db` (pico-sqlalchemy: SQLite on a volume by default, PostgreSQL via `DATABASE_URL`). A `git` driver (every mutation a commit, push to a protected remote) is planned; any new driver must pass the same contract test suite. The mounted directory (`CATALOG_PATH`) is a SEED: imported once when the database is empty, useful for bootstrapping from a catalog repository.

## Development

```bash
pip install -e ".[dev]"
pytest            # hermetic suite (pico-testing)
./smoke.sh        # docker compose end to end
```

## License

MIT
