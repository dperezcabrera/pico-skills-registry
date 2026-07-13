"""HTTP surface of the registry.

Every skill endpoint requires a valid JWT (pico-client-auth); results are
filtered by the caller's roles against each skill's access.groups, with
empty groups meaning any authenticated caller and admin seeing everything.
The embedded pico-server-auth issues the tokens; point auth_client at an
external pico-auth issuer to swap it out with zero code changes.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from pico_client_auth import SecurityContext
from pico_fastapi import controller, get
from pico_ioc import component, configured

from .catalog import Catalog, CatalogError


@configured(target="self", prefix="registry", mapping="tree")
@dataclass
class RegistrySettings:
    catalog_path: str = "example-catalog"


@component
class CatalogHolder:
    """Loads the catalog once at startup; fail-fast on an invalid one."""

    def __init__(self, settings: RegistrySettings):
        self.catalog = Catalog(Path(settings.catalog_path))


def _roles() -> set[str]:
    return set(SecurityContext.get_roles())


@controller(prefix="/api/v1/skills", tags=["Skills"])
class SkillsController:
    def __init__(self, holder: CatalogHolder):
        self._catalog = holder.catalog

    @get("")
    async def search(self, q: str, limit: int = 10):
        hits = self._catalog.search(q, _roles(), min(limit, 25))
        return [{"name": s.name, "description": s.description, "tags": s.tags} for s in hits]

    @get("/index")
    async def index(self):
        return [s.meta() for s in self._catalog.visible(_roles())]

    @get("/{name}")
    async def skill(self, name: str):
        s = self._catalog.skills.get(name)
        if s is None or not s.visible_to(_roles()):
            raise HTTPException(status_code=404, detail="no such skill")
        return {**s.meta(), "body": s.body}

    @get("/{name}/resources/{path:path}")
    async def resource(self, name: str, path: str):
        s = self._catalog.skills.get(name)
        if s is None or not s.visible_to(_roles()):
            raise HTTPException(status_code=404, detail="no such skill")
        try:
            file = self._catalog.resource_path(s, path)
        except CatalogError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return PlainTextResponse(file.read_text(encoding="utf-8"))
