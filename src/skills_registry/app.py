"""HTTP surface: reads filtered by groups, writes gated by assigned
permissions (registry.writer_roles), everything audited.

There is no anonymous path: pico-client-auth validates the JWT on every
request; the embedded pico-server-auth issues tokens (or point
auth_client at an external pico-auth issuer).
"""

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from pico_client_auth import SecurityContext, requires_role
from pico_fastapi import controller, get, post, put

from .contract import ContractError
from .service import Forbidden, RegistryService
from .store import SkillExists, SkillNotFound, VersionNotBumped


def _caller() -> tuple[str, set[str]]:
    claims = SecurityContext.require()
    return claims.sub, set(SecurityContext.get_roles())


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ContractError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, Forbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, SkillExists):
        return HTTPException(status_code=409, detail=f"ya existe: {exc}")
    if isinstance(exc, VersionNotBumped):
        return HTTPException(status_code=409, detail=f"version no incrementada: {exc}")
    if isinstance(exc, SkillNotFound):
        return HTTPException(status_code=404, detail="no such skill")
    raise exc


@controller(prefix="/api/v1/skills", tags=["Skills"])
class SkillsController:
    def __init__(self, service: RegistryService):
        self._service = service

    # ── lectura ──────────────────────────────────────────────────

    @get("")
    async def search(self, q: str, limit: int = 10):
        _, roles = _caller()
        hits = await self._service.search(q, roles, min(limit, 25))
        return [
            {"name": s.name, "version": s.version, "status": s.status, "description": s.description, "tags": s.tags}
            for s in hits
        ]

    @get("/index")
    async def index(self):
        _, roles = _caller()
        return [s.meta() for s in await self._service.visible(roles)]

    @get("/{name}")
    async def skill(self, name: str):
        _, roles = _caller()
        s = await self._service.get(name, roles)
        if s is None:
            raise HTTPException(status_code=404, detail="no such skill")
        return {**s.meta(), "body": s.body}

    @get("/{name}/versions")
    async def versions(self, name: str):
        _, roles = _caller()
        history = await self._service.versions(name, roles)
        if history is None:
            raise HTTPException(status_code=404, detail="no such skill")
        return history

    @get("/{name}/resources/{path:path}")
    async def resource(self, name: str, path: str):
        _, roles = _caller()
        content = await self._service.resource(name, path, roles)
        if content is None:
            raise HTTPException(status_code=404, detail="no such resource")
        return PlainTextResponse(content)

    # ── escritura (permisos asignados via registry.writer_roles) ─

    @post("")
    async def create(self, payload: dict):
        subject, roles = _caller()
        try:
            view = await self._service.create(subject, roles, payload)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc) from exc
        return view.meta()

    @put("/{name}")
    async def update(self, name: str, payload: dict):
        subject, roles = _caller()
        try:
            view = await self._service.update(subject, roles, {**payload, "name": name})
        except Exception as exc:  # noqa: BLE001
            raise _http(exc) from exc
        return view.meta()

    @post("/{name}/deprecate")
    async def deprecate(self, name: str, payload: dict | None = None):
        subject, roles = _caller()
        superseded_by = (payload or {}).get("superseded_by", "")
        try:
            view = await self._service.transition(subject, roles, name, "deprecated", superseded_by)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc) from exc
        return view.meta()

    @post("/{name}/retire")
    async def retire(self, name: str):
        subject, roles = _caller()
        try:
            view = await self._service.transition(subject, roles, name, "retired")
        except Exception as exc:  # noqa: BLE001
            raise _http(exc) from exc
        return {"name": view.name, "status": view.status}


@controller(prefix="/api/v1/audit", tags=["Audit"])
class AuditController:
    def __init__(self, service: RegistryService):
        self._service = service

    @requires_role("admin")
    @get("")
    async def audit(self, limit: int = 100):
        return await self._service.audit(min(limit, 500))
