---
name: rotate-secrets
version: 1.0.1
description: Rotate the fleet's CI secrets and reseed every repository.
triggers:
  - rotate the tokens
  - rotar secretos
  - reseed secrets
tags: [admin, security]
access:
  groups: [admin]
---

# rotate-secrets

Admin-only: describes the rotation procedure. Credentials are always
referenced (environment or vault path), never stored in the catalog -
the registry rejects catalogs containing credential-looking files.
