"""HTTP surface of the registry.

Every skill endpoint requires a valid JWT (pico-client-auth); results are
filtered by the caller's roles against each skill's access.groups, with
empty groups meaning any authenticated caller and admin seeing everything.
The embedded pico-server-auth issues the tokens; point auth_client at an
external pico-auth issuer to swap it out with zero code changes.

Lifecycle lives in the catalog repository (PRs, version bumps, status
transitions); the running registry follows it through POST /reload.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from pico_client_auth import SecurityContext, requires_role
from pico_fastapi import controller, get, post
from pico_ioc import component, configured

from .catalog import Catalog, CatalogError


@configured(target="self", prefix="registry", mapping="tree")
@dataclass
class RegistrySettings:
    catalog_path: str = "example-catalog"


@component
class CatalogHolder:
    """Loads the catalog at startup (fail-fast) and swaps it atomically on
    reload (fail-safe: an invalid new catalog keeps the old one serving)."""

    def __init__(self, settings: RegistrySettings):
        self._path = Path(settings.catalog_path)
        self.catalog = Catalog(self._path)

    def reload(self) -> Catalog:
        self.catalog = Catalog(self._path)  # raises CatalogError: caller keeps serving the old one
        return self.catalog


def _roles() -> set[str]:
    return set(SecurityContext.get_roles())


@controller(prefix="/api/v1/skills", tags=["Skills"])
class SkillsController:
    def __init__(self, holder: CatalogHolder):
        self._holder = holder

    @get("")
    async def search(self, q: str, limit: int = 10):
        hits = self._holder.catalog.search(q, _roles(), min(limit, 25))
        return [
            {"name": s.name, "version": s.version, "status": s.status, "description": s.description, "tags": s.tags}
            for s in hits
        ]

    @get("/index")
    async def index(self):
        return [s.meta() for s in self._holder.catalog.visible(_roles())]

    @get("/{name}")
    async def skill(self, name: str):
        s = self._holder.catalog.skills.get(name)
        if s is None or not s.visible_to(_roles()):
            raise HTTPException(status_code=404, detail="no such skill")
        return {**s.meta(), "body": s.body}

    @get("/{name}/resources/{path:path}")
    async def resource(self, name: str, path: str):
        s = self._holder.catalog.skills.get(name)
        if s is None or not s.visible_to(_roles()):
            raise HTTPException(status_code=404, detail="no such skill")
        try:
            file = self._holder.catalog.resource_path(s, path)
        except CatalogError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return PlainTextResponse(file.read_text(encoding="utf-8"))


@controller(prefix="/api/v1/catalog", tags=["Catalog"])
class CatalogAdminController:
    def __init__(self, holder: CatalogHolder):
        self._holder = holder

    @requires_role("admin")
    @post("/reload")
    async def reload(self):
        """Re-scan the mounted catalog (call it from the catalog repo's CD
        after merge). Atomic: an invalid catalog is rejected and the
        previous one keeps serving."""
        try:
            catalog = self._holder.reload()
        except CatalogError as e:
            raise HTTPException(status_code=422, detail=f"catalogo rechazado: {e}") from e
        return {"skills": len(catalog.skills)}
