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

    bad = tmp_path / "skills" / "leaky"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: leaky\ndescription: x\ntriggers: [x]\n---\nbody", encoding="utf-8")
    (bad / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    with pytest.raises(CatalogError, match="credencial"):
        Catalog(tmp_path)
