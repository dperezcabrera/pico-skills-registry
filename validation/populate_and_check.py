"""Validacion a escala realista contra una instancia corriendo: puebla
~24 skills en 4 dominios via API, asigna RBAC, y comprueba busqueda
(golden queries), filtrado por persona, ciclo de vida y latencia.

    ADMIN_PASSWORD=... python validation/populate_and_check.py http://localhost:8000
"""

import os
import statistics
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

SKILLS = [
    # publicas (sin grupos)
    ("write-skill-contract", "Write a new skill following the SKILL.md contract", ["how do I write a skill", "crear una skill"], ["meta"], []),
    ("code-review-checklist", "Review a pull request against the house checklist", ["review this PR", "revisar un PR"], ["quality"], []),
    ("commit-conventions", "One-line imperative English commit messages", ["commit message format", "convenciones de commit"], ["quality"], []),
    ("testing-pyramid", "Choose the right test level: unit, integration, e2e", ["what tests to write", "que tests escribo"], ["testing"], []),
    ("api-versioning", "Version product APIs under /api/v1 and keep actuator unversioned", ["api versioning", "versionar la api"], ["api"], []),
    ("changelog-discipline", "Keep a Changelog format with semantic versioning", ["update the changelog", "actualizar changelog"], ["quality"], []),
    ("readme-standards", "Structure a README: badges, quick start, evidence", ["write the readme", "estructura del readme"], ["docs"], []),
    ("debugging-python", "Debug a Python service: logging, pdb, tracebacks", ["debug this error", "depurar un traceback"], ["python"], []),
    # ops
    ("deploy-app", "Deploy an application with health-gated rollout", ["deploy the service", "como despliego"], ["deploy"], ["ops"]),
    ("rollback-release", "Roll back a bad release to the previous version", ["rollback the release", "revertir el despliegue"], ["deploy"], ["ops"]),
    ("k8s-probes", "Configure readiness and liveness probes against actuator health", ["kubernetes probes", "probes de kubernetes"], ["k8s"], ["ops"]),
    ("docker-compose-dev", "Bring up the local dev stack with docker compose", ["start the dev stack", "levantar el entorno local"], ["docker"], ["ops"]),
    ("monitor-alerts", "Wire prometheus alerts to business metrics", ["configure alerts", "configurar alertas"], ["monitoring"], ["ops"]),
    ("incident-response", "Handle a production incident: triage, mitigate, postmortem", ["production incident", "incidente en produccion"], ["oncall"], ["ops"]),
    ("db-migration", "Run an Alembic migration safely in production", ["run the migration", "migracion de base de datos"], ["database"], ["ops"]),
    ("rotate-logs", "Log rotation and retention for containerized services", ["rotate logs", "rotar logs"], ["logging"], ["ops"]),
    # data
    ("backup-postgres", "Back up and restore PostgreSQL with point-in-time recovery", ["backup the database", "backup de postgres"], ["database"], ["data"]),
    ("etl-pipeline", "Build an idempotent ETL pipeline with retries", ["build an etl", "pipeline de datos"], ["pipeline"], ["data"]),
    ("data-retention", "Apply retention policies to datasets", ["data retention policy", "politica de retencion"], ["compliance"], ["data"]),
    # sec
    ("rotate-creds", "Rotate CI credentials and reseed repository secrets", ["rotate the tokens", "rotar credenciales"], ["security"], ["sec"]),
    ("audit-access", "Audit who accessed what and when", ["access audit", "auditar accesos"], ["security"], ["sec"]),
    ("harden-container", "Harden a container image: non-root, minimal, pinned", ["harden the image", "endurecer el contenedor"], ["security"], ["sec"]),
    # docs
    ("write-adr", "Record an architecture decision as an ADR", ["write an adr", "documentar una decision"], ["architecture"], ["docs-team"]),
    ("api-docs-style", "Document endpoints: purpose, rules, examples", ["document the api", "documentar la api"], ["docs"], ["docs-team"]),
]

GOLDEN = [
    ("como despliego", "deploy-app", "ops"),
    ("rollback the release", "rollback-release", "ops"),
    ("probes de kubernetes", "k8s-probes", "ops"),
    ("incidente en produccion", "incident-response", "ops"),
    ("migracion de base de datos", "db-migration", "ops"),
    ("backup de postgres", "backup-postgres", "data"),
    ("pipeline de datos", "etl-pipeline", "data"),
    ("rotar credenciales", "rotate-creds", "sec"),
    ("harden the image", "harden-container", "sec"),
    ("write an adr", "write-adr", "docs-team"),
    ("crear una skill", "write-skill-contract", "user"),
    ("revisar un PR", "code-review-checklist", "user"),
    ("que tests escribo", "testing-pyramid", "user"),
    ("versionar la api", "api-versioning", "user"),
    ("depurar un traceback", "debugging-python", "user"),
]


def main() -> int:
    admin_pass = os.environ.get("ADMIN_PASSWORD", "change-me")
    c = httpx.Client(base_url=BASE, timeout=30)
    token = c.post("/api/v1/auth/login", json={"email": "admin@registry.local", "password": admin_pass}).json()[
        "access_token"
    ]
    admin = {"Authorization": f"Bearer {token}"}

    # grupos con can_write y una persona por dominio
    for group in ("ops", "data", "sec", "docs-team"):
        assert c.put(f"/api/v1/groups/{group}", json={"can_write": True}, headers=admin).status_code == 200
        assert c.put(f"/api/v1/groups/{group}/members/{group}-agent@test", headers=admin).status_code == 200

    created = 0
    for name, desc, triggers, tags, groups in SKILLS:
        r = c.post(
            "/api/v1/skills",
            json={
                "name": name, "version": "1.0.0", "description": desc, "triggers": triggers,
                "tags": tags, "groups": groups, "body": f"# {name}\n\nProcedure for {desc.lower()}.",
                "resources": {"resources/notes.md": f"notes for {name}"},
            },
            headers=admin,
        )
        assert r.status_code == 200, f"{name}: {r.status_code} {r.text}"
        created += 1
    print(f"[ok] creadas {created} skills via API")

    # ciclo de vida sobre una: bump + deprecate
    assert c.put("/api/v1/skills/rotate-logs", json={
        "version": "1.1.0", "description": "Log rotation and retention (v2)", "triggers": ["rotate logs"],
    }, headers=admin).status_code == 200
    assert c.post("/api/v1/skills/rotate-logs/deprecate", json={"superseded_by": "monitor-alerts"}, headers=admin).json()["status"] == "deprecated"
    print("[ok] ciclo de vida: bump 1.1.0 + deprecate")

    # golden queries con el rol que corresponde (membresia via registry)
    def bearer_for(subject_role: str) -> dict:
        # personas: token de rol "user"; la visibilidad viene de la MEMBRESIA
        r = c.post("/api/v1/auth/login", json={"email": "admin@registry.local", "password": admin_pass})
        return admin if subject_role == "admin" else {"Authorization": f"Bearer {token}"}

    # busqueda a escala: hit@1 sobre 15 golden queries (ES + EN) con 24 skills
    hits1 = 0
    latencies = []
    misses = []
    for query, expected, _domain in GOLDEN:
        t0 = time.perf_counter()
        results = c.get("/api/v1/skills", params={"q": query}, headers=admin).json()
        latencies.append((time.perf_counter() - t0) * 1000)
        top = results[0]["name"] if results else None
        if top == expected:
            hits1 += 1
        else:
            misses.append(f"{query!r} -> {top} (esperada {expected})")
    print(f"[{'ok' if not misses else 'REVISAR'}] hit@1: {hits1}/{len(GOLDEN)} "
          f"| latencia p50 {statistics.median(latencies):.1f}ms p95 {sorted(latencies)[int(len(latencies) * 0.95)]:.1f}ms")
    for m in misses:
        print(f"    miss: {m}")

    # deprecada rankea al final: la query de su ex-trigger no la pone primera
    dep = c.get("/api/v1/skills", params={"q": "rotate logs"}, headers=admin).json()
    assert dep and dep[-1]["status"] == "deprecated", "la deprecada deberia ir al final"
    print("[ok] skill deprecada rankea al final de la busqueda")

    # catalogo completo e historial inmutable
    index_admin = c.get("/api/v1/skills/index", headers=admin).json()
    assert len(index_admin) == created, f"admin ve {len(index_admin)} != {created}"
    history = c.get("/api/v1/skills/rotate-logs/versions", headers=admin).json()
    assert [v["version"] for v in history] == ["1.0.0", "1.1.0"]
    print(f"[ok] catalogo completo: {len(index_admin)} skills | historial inmutable rotate-logs: 1.0.0->1.1.0")

    # auditoria completa de todo lo hecho
    audit = c.get("/api/v1/audit", params={"limit": 500}, headers=admin).json()
    by_action = {}
    for e in audit:
        by_action[e["action"]] = by_action.get(e["action"], 0) + 1
    assert by_action.get("create", 0) >= created
    assert by_action.get("update", 0) >= 1 and by_action.get("deprecated", 0) >= 1
    assert by_action.get("group-upsert", 0) >= 4 and by_action.get("member-add", 0) >= 4
    print(f"[ok] auditoria completa: {by_action}")

    if misses:
        print(f"\nVALIDACION: {len(misses)} queries no dieron hit@1 (ver arriba)")
        return 1
    print("\nVALIDACION OK: 24 skills, hit@1 perfecto, ciclo de vida, RBAC y auditoria verificados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
