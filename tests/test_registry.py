from pathlib import Path

import pytest

CATALOG = str(Path(__file__).resolve().parent.parent / "example-catalog")

CONFIG = {
    "fastapi": {"title": "registry-test"},
    "registry": {"catalog_path": CATALOG},
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
def harness(make_container, make_client, monkeypatch):
    container = make_container("pico_fastapi", "pico_server_auth", "pico_client_auth", "pico_actuator", config=CONFIG)
    client = make_client(container)

    from pico_client_auth.jwks_client import JWKSClient

    jwks = client.get("/api/v1/auth/jwks").json()

    async def _fetch_captured(self):
        self._keys = {k["kid"]: k for k in jwks["keys"]}
        self._fetched_at = float("inf")

    monkeypatch.setattr(JWKSClient, "_fetch_keys", _fetch_captured)
    return client, container


def bearer(container, role: str) -> dict:
    from pico_server_auth import TokenIssuer

    token = container.get(TokenIssuer).issue_access_token(subject=f"{role}@test", role=role)
    return {"Authorization": f"Bearer {token}"}


def test_anonymous_gets_nothing(harness):
    client, _ = harness
    assert client.get("/api/v1/skills?q=deploy").status_code == 401


def test_search_filters_by_group(harness):
    client, container = harness
    names = {s["name"] for s in client.get("/api/v1/skills?q=deploy", headers=bearer(container, "user")).json()}
    assert "deploy-service" not in names  # gated to ops

    names = {s["name"] for s in client.get("/api/v1/skills?q=deploy", headers=bearer(container, "ops")).json()}
    assert "deploy-service" in names


def test_admin_sees_everything_in_index(harness):
    client, container = harness
    names = {s["name"] for s in client.get("/api/v1/skills/index", headers=bearer(container, "admin")).json()}
    assert names == {"hello-world", "deploy-service", "rotate-secrets"}

    names = {s["name"] for s in client.get("/api/v1/skills/index", headers=bearer(container, "user")).json()}
    assert names == {"hello-world"}


def test_gated_skill_is_invisible_not_forbidden(harness):
    client, container = harness
    # a caller without the group cannot even confirm the skill exists
    assert client.get("/api/v1/skills/rotate-secrets", headers=bearer(container, "user")).status_code == 404
    assert client.get("/api/v1/skills/rotate-secrets", headers=bearer(container, "admin")).status_code == 200


def test_fetch_skill_body_and_resource(harness):
    client, container = harness
    ops = bearer(container, "ops")
    skill = client.get("/api/v1/skills/deploy-service", headers=ops).json()
    assert "rollout" in skill["body"] and skill["sha256"] and skill["resources"] == ["resources/rollout.sh"]

    resource = client.get("/api/v1/skills/deploy-service/resources/resources/rollout.sh", headers=ops)
    assert resource.status_code == 200 and "readiness probe" in resource.text


def test_path_traversal_is_rejected(harness):
    client, container = harness
    r = client.get(
        "/api/v1/skills/deploy-service/resources/../../rotate-secrets/SKILL.md",
        headers=bearer(container, "ops"),
    )
    assert r.status_code == 404


def test_search_by_spanish_trigger(harness):
    client, container = harness
    hits = client.get("/api/v1/skills?q=como despliego", headers=bearer(container, "ops")).json()
    assert hits and hits[0]["name"] == "deploy-service"


def test_catalog_rejects_credential_files(tmp_path):
    from skills_registry.catalog import Catalog, CatalogError

    _write_skill(tmp_path, "leaky")
    (tmp_path / "skills" / "leaky" / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    with pytest.raises(CatalogError, match="credencial"):
        Catalog(tmp_path)


def _write_skill(root, name, version="1.0.0", status="active", extra=""):
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nversion: {version}\ndescription: about {name}\n"
        f"triggers: [use {name}]\nstatus: {status}\n{extra}---\nbody of {name}",
        encoding="utf-8",
    )


def test_version_is_mandatory(tmp_path):
    from skills_registry.catalog import Catalog, CatalogError

    d = tmp_path / "skills" / "unversioned"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: unversioned\ndescription: x\ntriggers: [x]\n---\nb", encoding="utf-8")
    with pytest.raises(CatalogError, match="version"):
        Catalog(tmp_path)


def test_lifecycle_deprecated_ranks_last_and_retired_is_hidden(tmp_path):
    from skills_registry.catalog import Catalog

    _write_skill(tmp_path, "widget-v2", version="2.0.0")
    _write_skill(tmp_path, "widget-old", status="deprecated", extra="superseded_by: widget-v2\n")
    _write_skill(tmp_path, "widget-dead", status="retired")
    catalog = Catalog(tmp_path)

    names = [s.name for s in catalog.search("widget use", roles={"user"})]
    assert "widget-dead" not in names
    assert names.index("widget-v2") < names.index("widget-old")
    meta = catalog.skills["widget-old"].meta()
    assert meta["status"] == "deprecated" and meta["superseded_by"] == "widget-v2"


def test_reload_is_admin_only_and_atomic(harness, tmp_path, monkeypatch):
    client, container = harness
    from skills_registry.app import CatalogHolder
    from skills_registry.catalog import Catalog

    _write_skill(tmp_path, "first")
    holder = container.get(CatalogHolder)
    monkeypatch.setattr(holder, "_path", tmp_path)
    monkeypatch.setattr(holder, "catalog", Catalog(tmp_path))

    assert client.post("/api/v1/catalog/reload", headers=bearer(container, "user")).status_code == 403

    _write_skill(tmp_path, "second")
    r = client.post("/api/v1/catalog/reload", headers=bearer(container, "admin"))
    assert r.status_code == 200 and r.json() == {"skills": 2}

    # un catalogo invalido se rechaza y el anterior sigue sirviendo
    (tmp_path / "skills" / "second" / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    assert client.post("/api/v1/catalog/reload", headers=bearer(container, "admin")).status_code == 422
    assert len(holder.catalog.skills) == 2
