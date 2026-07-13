#!/usr/bin/env bash
# Example resource: health-gated rollout (illustrative).
set -euo pipefail
IMAGE="${1:?usage: rollout.sh <image>}"
echo "applying manifest for $IMAGE"
echo "waiting for readiness probe..."
echo "rollout complete"
