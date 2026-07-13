#!/usr/bin/env bash
# End-to-end smoke: boot the image, login, search, fetch a gated skill and
# a resource with the admin token.
set -euo pipefail
cd "$(dirname "$0")"

cleanup() { docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

ADMIN_PASSWORD=smoke-secret docker compose up -d --build

echo "waiting for the registry healthcheck..."
for i in $(seq 1 30); do
  state=$(docker compose ps --format '{{.Health}}' registry 2>/dev/null || echo starting)
  [ "$state" = "healthy" ] && break
  sleep 2
done
[ "$state" = "healthy" ] || { echo "registry never became healthy"; docker compose logs registry | tail -20; exit 1; }

token=$(curl -sf -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@registry.local", "password": "smoke-secret"}' | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
[ -n "$token" ] || { echo "login failed"; exit 1; }
AUTH="Authorization: Bearer $token"

curl -sf localhost:8000/api/v1/skills?q=deploy -H "$AUTH" | grep -q deploy-service
curl -sf localhost:8000/api/v1/skills/rotate-secrets -H "$AUTH" | grep -q sha256
curl -sf localhost:8000/api/v1/skills/deploy-service/resources/resources/rollout.sh -H "$AUTH" | grep -q "readiness probe"
curl -s -o /dev/null -w '%{http_code}' localhost:8000/api/v1/skills?q=deploy | grep -q 401

echo "SMOKE OK: login, busqueda, skill con grupo, recurso y 401 anonimo verificados"
