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

## Lifecycle

Skills are dynamic, but their lifecycle lives in the catalog repository, not in a write API - creation and updates go through pull requests (agents included: an agent proposes a skill by opening a PR, the gates run, a human merges), so the review gate is never bypassed.

- **Version**: mandatory `version: X.Y.Z` per skill; the catalog CI rejects content changes without a bump. The index publishes version plus content hash.
- **Deprecate**: `status: deprecated` (optionally `superseded_by: other-skill`) keeps the skill served but ranked last in search and flagged in every response; `status: retired` hides it entirely while its history stays in git.
- **Hot reload**: `POST /api/v1/catalog/reload` (admin) re-scans the mounted catalog without restarting - call it from the catalog's CD after merge (or a git-sync sidecar). Atomic and fail-safe: an invalid catalog is rejected with a 422 and the previous one keeps serving.

## Development

```bash
pip install -e ".[dev]"
pytest            # hermetic suite (pico-testing)
./smoke.sh        # docker compose end to end
```

## License

MIT
