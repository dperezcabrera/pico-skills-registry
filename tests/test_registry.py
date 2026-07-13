from pathlib import Path

import pytest

SEED = str(Path(__file__).resolve().parent.parent / "example-catalog")


def config(tmp_path, writer_roles=None):
    return {
        "fastapi": {"title": "registry-test"},
        "registry": {"seed_path": SEED, "writer_roles": writer_roles or ["admin", "ops"]},
        "database": {"url": f"sqlite+aiosqlite:///{tmp_path}/registry.db"},
        "server_auth": {
            "issuer": "http://registry.local",
            "audience": "skills-registry",
            "auto_create_admin": True,
            "admin_email": "admin@registry.local",
            "admin_password": "secret",
            "admin_role": "admin",
        },
        "auth_client": {"enabled": True, "issuer": "http://registry.local", "audience": "skills-registry"},
        "actuator": {},
    }


@pytest.fixture
def harness(make_container, make_client, monkeypatch, tmp_path):
    container = make_container(
        "pico_fastapi",
        "pico_sqlalchemy",
        "pico_server_auth",
        "pico_client_auth",
        "pico_actuator",
        config=config(tmp_path),
    )
    client = make_client(container)

    from pico_client_auth.jwks_client import JWKSClient

    jwks = client.get("/api/v1/auth/jwks").json()

    async def _fetch_captured(self):
        self._keys = {k["kid"]: k for k in jwks["keys"]}
        self._fetched_at = float("inf")

    monkeypatch.setattr(JWKSClient, "_fetch_keys", _fetch_captured)
    return client, container


def bearer(container, role: str, subject: str = "") -> dict:
    from pico_server_auth import TokenIssuer

    token = container.get(TokenIssuer).issue_access_token(subject=subject or f"{role}@test", role=role)
    return {"Authorization": f"Bearer {token}"}


def payload(name="widget", version="1.0.0", groups=None, **extra):
    return {
        "name": name,
        "version": version,
        "description": f"about {name}",
        "triggers": [f"use {name}"],
        "groups": groups or [],
        **extra,
    }


# ── lectura (semantica previa preservada) ────────────────────────


def test_anonymous_gets_nothing(harness):
    client, _ = harness
    assert client.get("/api/v1/skills?q=deploy").status_code == 401


def test_seed_import_and_group_filtering(harness):
    client, container = harness
    names = {s["name"] for s in client.get("/api/v1/skills/index", headers=bearer(container, "admin")).json()}
    assert names == {"hello-world", "deploy-service", "rotate-secrets"}
    names = {s["name"] for s in client.get("/api/v1/skills/index", headers=bearer(container, "user")).json()}
    assert names == {"hello-world"}
    assert client.get("/api/v1/skills/rotate-secrets", headers=bearer(container, "user")).status_code == 404


def test_resource_and_spanish_trigger(harness):
    client, container = harness
    ops = bearer(container, "ops")
    hits = client.get("/api/v1/skills?q=como despliego", headers=ops).json()
    assert hits and hits[0]["name"] == "deploy-service"
    r = client.get("/api/v1/skills/deploy-service/resources/resources/rollout.sh", headers=ops)
    assert r.status_code == 200 and "readiness probe" in r.text


# ── escritura por permisos asignados ─────────────────────────────


def test_reader_role_cannot_write(harness):
    client, container = harness
    r = client.post("/api/v1/skills", json=payload(), headers=bearer(container, "user"))
    assert r.status_code == 403


def test_writer_creates_and_search_finds_it(harness):
    client, container = harness
    ops = bearer(container, "ops")
    r = client.post("/api/v1/skills", json=payload(groups=["ops"]), headers=ops)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "1.0.0" and r.json()["sha256"]
    assert client.get("/api/v1/skills?q=widget", headers=ops).json()[0]["name"] == "widget"


def test_writer_cannot_publish_outside_own_groups(harness):
    client, container = harness
    r = client.post("/api/v1/skills", json=payload(groups=["finance"]), headers=bearer(container, "ops"))
    assert r.status_code == 403


def test_update_requires_bump_and_creates_immutable_version(harness):
    client, container = harness
    ops = bearer(container, "ops", subject="alice@test")
    client.post("/api/v1/skills", json=payload(), headers=ops)

    same = client.put("/api/v1/skills/widget", json=payload(version="1.0.0"), headers=ops)
    assert same.status_code == 409

    bumped = client.put("/api/v1/skills/widget", json=payload(version="1.1.0"), headers=ops)
    assert bumped.status_code == 200
    history = client.get("/api/v1/skills/widget/versions", headers=ops).json()
    assert [v["version"] for v in history] == ["1.0.0", "1.1.0"]


def test_only_author_or_admin_update(harness):
    client, container = harness
    client.post("/api/v1/skills", json=payload(), headers=bearer(container, "ops", subject="alice@test"))
    r = client.put(
        "/api/v1/skills/widget", json=payload(version="1.1.0"), headers=bearer(container, "ops", subject="eve@test")
    )
    assert r.status_code == 403
    r = client.put("/api/v1/skills/widget", json=payload(version="1.1.0"), headers=bearer(container, "admin"))
    assert r.status_code == 200


def test_lifecycle_deprecate_then_retire(harness):
    client, container = harness
    ops = bearer(container, "ops")
    client.post("/api/v1/skills", json=payload(), headers=ops)

    r = client.post("/api/v1/skills/widget/deprecate", json={"superseded_by": "widget2"}, headers=ops)
    assert r.json()["status"] == "deprecated" and r.json()["superseded_by"] == "widget2"
    assert client.get("/api/v1/skills/widget", headers=ops).json()["status"] == "deprecated"

    assert client.post("/api/v1/skills/widget/retire", headers=ops).json()["status"] == "retired"
    assert client.get("/api/v1/skills/widget", headers=ops).status_code == 404


def test_credential_resource_rejected(harness):
    client, container = harness
    bad = payload(resources={"resources/id_rsa": "PRIVATE"})
    r = client.post("/api/v1/skills", json=bad, headers=bearer(container, "ops"))
    assert r.status_code == 422 and "credencial" in r.json()["detail"]


def test_audit_trail_is_admin_only_and_complete(harness):
    client, container = harness
    ops = bearer(container, "ops", subject="alice@test")
    client.post("/api/v1/skills", json=payload(), headers=ops)
    client.put("/api/v1/skills/widget", json=payload(version="1.1.0"), headers=ops)
    client.post("/api/v1/skills/widget/deprecate", headers=ops)

    assert client.get("/api/v1/audit", headers=ops).status_code == 403
    entries = client.get("/api/v1/audit", headers=bearer(container, "admin")).json()
    actions = [(e["action"], e["subject"]) for e in entries if e["skill"] == "widget"]
    assert actions == [("deprecated", "alice@test"), ("update", "alice@test"), ("create", "alice@test")]


# ── validador de directorios (CI de catalogos) ───────────────────


def test_dir_catalog_rejects_credential_files(tmp_path):
    from skills_registry.catalog import Catalog, CatalogError

    d = tmp_path / "skills" / "leaky"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: leaky\nversion: 1.0.0\ndescription: x\ntriggers: [x]\n---\nb", encoding="utf-8"
    )
    (d / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    with pytest.raises(CatalogError, match="credencial"):
        Catalog(tmp_path)


def test_dir_catalog_golden_search(tmp_path):
    from skills_registry.catalog import Catalog

    d = tmp_path / "skills" / "deploy-thing"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-thing\nversion: 1.0.0\ndescription: deploy\ntriggers: [como despliego]\n---\nb",
        encoding="utf-8",
    )
    hits = Catalog(tmp_path).search("como despliego", roles={"admin"})
    assert hits and hits[0].name == "deploy-thing"


# ── fail-fast en el arranque ─────────────────────────────────────


def test_invalid_seed_kills_the_boot(make_container, tmp_path):
    bad = tmp_path / "catalog" / "skills" / "leaky"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: leaky\nversion: 1.0.0\ndescription: x\ntriggers: [x]\n---\nb", encoding="utf-8"
    )
    (bad / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    cfg = config(tmp_path)
    cfg["registry"]["seed_path"] = str(tmp_path / "catalog")
    with pytest.raises(Exception, match="credencial"):
        make_container(
            "pico_fastapi", "pico_sqlalchemy", "pico_server_auth", "pico_client_auth", "pico_actuator", config=cfg
        )


def test_unknown_backend_kills_the_boot(make_container, tmp_path):
    cfg = config(tmp_path)
    cfg["registry"]["backend"] = "git"
    with pytest.raises(Exception, match="no implementado"):
        make_container(
            "pico_fastapi", "pico_sqlalchemy", "pico_server_auth", "pico_client_auth", "pico_actuator", config=cfg
        )


def test_health_reflects_loaded_catalog(harness):
    client, container = harness
    health = client.get("/actuator/health")
    assert health.status_code == 200
    assert '"registry"' in health.text and '"skills"' in health.text


# ── RBAC: permisos a grupos, usuarios/agentes a grupos ───────────


def test_membership_grants_visibility_without_new_token(harness):
    client, container = harness
    alice = bearer(container, "user", subject="alice@test")
    admin = bearer(container, "admin")

    # con su token de rol "user" no ve las skills de ops
    assert client.get("/api/v1/skills/deploy-service", headers=alice).status_code == 404

    client.put("/api/v1/groups/ops", json={"can_write": False}, headers=admin)
    client.put("/api/v1/groups/ops/members/alice@test", headers=admin)

    # MISMO token: la membresia surte efecto sin reemitir nada
    assert client.get("/api/v1/skills/deploy-service", headers=alice).status_code == 200
    me = client.get("/api/v1/me", headers=alice).json()
    assert "ops" in me["groups"] and me["can_write"] is False

    client.delete("/api/v1/groups/ops/members/alice@test", headers=admin)
    assert client.get("/api/v1/skills/deploy-service", headers=alice).status_code == 404


def test_can_write_group_grants_write_permission(harness):
    client, container = harness
    bob = bearer(container, "user", subject="bob@test")
    admin = bearer(container, "admin")

    assert client.post("/api/v1/skills", json=payload(name="bobs"), headers=bob).status_code == 403

    client.put("/api/v1/groups/authors", json={"can_write": True}, headers=admin)
    client.put("/api/v1/groups/authors/members/bob@test", headers=admin)

    r = client.post("/api/v1/skills", json=payload(name="bobs", groups=["authors"]), headers=bob)
    assert r.status_code == 200, r.text


def test_group_management_is_admin_only_and_audited(harness):
    client, container = harness
    ops = bearer(container, "ops")
    admin = bearer(container, "admin")

    assert client.put("/api/v1/groups/x", json={"can_write": True}, headers=ops).status_code == 403
    assert client.get("/api/v1/groups", headers=ops).status_code == 403

    client.put("/api/v1/groups/qa", json={"can_write": False}, headers=admin)
    client.put("/api/v1/groups/qa/members/carol@test", headers=admin)
    groups = client.get("/api/v1/groups", headers=admin).json()
    assert {"name": "qa", "can_write": False, "members": ["carol@test"]} in groups

    actions = [e["action"] for e in client.get("/api/v1/audit", headers=admin).json()]
    assert "group-upsert" in actions and "member-add" in actions


def test_membership_in_unknown_group_is_404(harness):
    client, container = harness
    r = client.put("/api/v1/groups/ghost/members/x@test", headers=bearer(container, "admin"))
    assert r.status_code == 404
