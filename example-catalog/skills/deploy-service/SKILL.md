---
name: deploy-service
version: 1.2.0
description: Deploy a service to the cluster with health-gated rollout.
triggers:
  - deploy the service
  - roll out to production
  - como despliego
tags: [ops, deploy]
access:
  groups: [ops]
tools:
  - server: k8s
    tools: [apply_manifest]
---

# deploy-service

Gated to the ops group: only callers whose token carries the ops (or
admin) role can see or fetch this skill.

Run resources/rollout.sh with the target image; it applies the manifest
and waits for the readiness probe before shifting traffic.
