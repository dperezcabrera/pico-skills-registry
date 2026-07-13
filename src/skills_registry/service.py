"""Domain service: authorization, search snapshot and the seed import.

Authorization model ("los que tengan permisos asignados"): roles listed in
``registry.writer_roles`` may create skills for groups they belong to
(admin for any group); updating or transitioning an existing skill is for
its author or admin. Reads filter by access.groups against the caller's
roles. All storage goes through the SkillStore port.
"""

import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from pico_ioc import component, configured

from .contract import ContractError, content_hash, parse_skill_md, validate_payload
from .store import SkillView, SqlSkillStore

logger = logging.getLogger(__name__)


class Forbidden(Exception):
    pass


@configured(target="self", prefix="registry", mapping="tree")
@dataclass
class RegistrySettings:
    backend: str = "db"  # unico driver disponible; "git" esta planificado
    seed_path: str = ""  # directorio de catalogo importado si la BD esta vacia
    writer_roles: list[str] = field(default_factory=lambda: ["admin"])


@component
class RegistryService:
    def __init__(self, settings: RegistrySettings, store: SqlSkillStore):
        if settings.backend != "db":
            raise ValueError(f"backend '{settings.backend}' no implementado (disponible: db; planificado: git)")
        self._settings = settings
        self._store = store
        self._skills: dict[str, SkillView] = {}
        self._index = None
        self._writable: dict[str, bool] = {}
        self._memberships: dict[str, set[str]] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def size(self) -> int:
        return len(self._skills)

    # ── snapshot y busqueda ──────────────────────────────────────

    async def load(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            views = await self._store.load_all()
            if not views and self._settings.seed_path:
                views = await self._seed(Path(self._settings.seed_path))
            self._replace_snapshot(views)
            self._writable, self._memberships = await self._store.rbac_snapshot()
            self._loaded = True

    def _replace_snapshot(self, views: list[SkillView]) -> None:
        self._skills = {v.name: v for v in views}
        index = sqlite3.connect(":memory:", check_same_thread=False)
        index.execute("CREATE VIRTUAL TABLE skills USING fts5(name, description, triggers, tags)")
        index.executemany(
            "INSERT INTO skills VALUES (?, ?, ?, ?)",
            [(v.name, v.description, " ".join(v.triggers), " ".join(v.tags)) for v in views],
        )
        self._index = index

    def _refresh(self, view: SkillView) -> None:
        self._skills[view.name] = view
        self._replace_snapshot(list(self._skills.values()))

    async def _seed(self, root: Path) -> list[SkillView]:
        views = []
        for skill_dir in sorted(p for p in (root / "skills").iterdir() if p.is_dir()):
            resources = {
                str(f.relative_to(skill_dir)): f.read_text(encoding="utf-8")
                for f in sorted(skill_dir.rglob("*"))
                if f.is_file() and f.name != "SKILL.md"
            }
            norm = parse_skill_md(skill_dir.name, (skill_dir / "SKILL.md").read_text(encoding="utf-8"), resources)
            views.append(await self._store.create("seed", "seed", norm, content_hash(norm)))
        logger.info("seed importado: %d skills desde %s", len(views), root)
        return views

    def effective(self, subject: str, roles: set[str]) -> set[str]:
        """Grupos efectivos: roles del token + membresias asignadas en el registry."""
        return roles | self._memberships.get(subject, set())

    async def search(self, query: str, subject: str, roles: set[str], limit: int = 10) -> list[SkillView]:
        await self.load()
        roles = self.effective(subject, roles)
        sanitized = " ".join(re.findall(r"\w+", query))
        if not sanitized:
            return []
        rows = self._index.execute(
            "SELECT name FROM skills WHERE skills MATCH ? ORDER BY bm25(skills) LIMIT ?",
            (" OR ".join(sanitized.split()), limit * 3),
        ).fetchall()
        hits = [self._skills[name] for (name,) in rows if self._skills[name].visible_to(roles)]
        hits.sort(key=lambda s: s.status != "active")
        return hits[:limit]

    async def visible(self, subject: str, roles: set[str]) -> list[SkillView]:
        await self.load()
        effective = self.effective(subject, roles)
        return [s for s in self._skills.values() if s.visible_to(effective)]

    async def get(self, name: str, subject: str, roles: set[str]) -> SkillView | None:
        await self.load()
        skill = self._skills.get(name)
        return skill if skill is not None and skill.visible_to(self.effective(subject, roles)) else None

    async def resource(self, name: str, path: str, subject: str, roles: set[str]) -> str | None:
        if await self.get(name, subject, roles) is None:
            return None
        return await self._store.resource(name, path)

    async def versions(self, name: str, subject: str, roles: set[str]) -> list[dict] | None:
        if await self.get(name, subject, roles) is None:
            return None
        return await self._store.versions(name)

    async def audit(self, limit: int = 100) -> list[dict]:
        return await self._store.audit(limit)

    # ── escritura ────────────────────────────────────────────────

    def _authorize_writer(self, subject: str, roles: set[str], groups: list[str]) -> None:
        effective = self.effective(subject, roles)
        writer_by_role = bool(roles & set(self._settings.writer_roles))
        writer_by_group = any(self._writable.get(g, False) for g in effective)
        if not (writer_by_role or writer_by_group):
            raise Forbidden("sin permiso de escritura (ni rol escritor ni grupo con can_write)")
        if "admin" not in roles and not set(groups) <= effective:
            raise Forbidden("no puedes publicar para grupos a los que no perteneces")

    def _authorize_owner(self, subject: str, roles: set[str], skill: SkillView) -> None:
        if "admin" not in roles and skill.author != subject:
            raise Forbidden("solo el autor o admin modifican una skill existente")

    async def create(self, subject: str, roles: set[str], payload: dict) -> SkillView:
        await self.load()
        norm = validate_payload(payload)
        self._authorize_writer(subject, roles, norm["groups"])
        view = await self._store.create(subject, ",".join(sorted(roles)), norm, content_hash(norm))
        self._refresh(view)
        return view

    async def update(self, subject: str, roles: set[str], payload: dict) -> SkillView:
        await self.load()
        name = str(payload.get("name", ""))
        current = self._skills.get(name)
        if current is None:
            from .store import SkillNotFound

            raise SkillNotFound(name)
        # access metadata persists across updates unless explicitly changed:
        # a PUT that omits groups must NOT silently make a gated skill public
        inherited = {
            key: getattr(current, key)
            for key in ("groups", "tags", "tools")
            if key not in payload
        }
        norm = validate_payload({**inherited, **payload})
        self._authorize_writer(subject, roles, norm["groups"])
        self._authorize_owner(subject, roles, current)
        view = await self._store.update(subject, ",".join(sorted(roles)), norm, content_hash(norm))
        self._refresh(view)
        return view

    async def transition(
        self, subject: str, roles: set[str], name: str, status: str, superseded_by: str = ""
    ) -> SkillView:
        await self.load()
        if status not in ("active", "deprecated", "retired"):
            raise ContractError(f"status invalido '{status}'")
        current = self._skills.get(name)
        if current is None:
            from .store import SkillNotFound

            raise SkillNotFound(name)
        self._authorize_writer(subject, roles, current.groups)
        self._authorize_owner(subject, roles, current)
        view = await self._store.transition(subject, ",".join(sorted(roles)), name, status, superseded_by)
        self._refresh(view)
        return view

    # ── grupos y membresias (admin) ──────────────────────────────

    async def groups(self) -> list[dict]:
        await self.load()
        return await self._store.groups()

    async def upsert_group(self, subject: str, roles: set[str], name: str, can_write: bool) -> dict:
        await self.load()
        result = await self._store.upsert_group(subject, ",".join(sorted(roles)), name, can_write)
        self._writable, self._memberships = await self._store.rbac_snapshot()
        return result

    async def set_membership(self, subject: str, roles: set[str], group: str, member: str, add: bool) -> None:
        await self.load()
        await self._store.set_membership(subject, ",".join(sorted(roles)), group, member, add)
        self._writable, self._memberships = await self._store.rbac_snapshot()

    async def whoami(self, subject: str, roles: set[str]) -> dict:
        await self.load()
        effective = self.effective(subject, roles)
        return {
            "subject": subject,
            "roles": sorted(roles),
            "groups": sorted(effective),
            "can_write": bool(roles & set(self._settings.writer_roles))
            or any(self._writable.get(g, False) for g in effective),
        }


@component
class RegistryWarmup:
    """Fail-fast: BD accesible y seed valido se comprueban en el ARRANQUE,
    no en la primera peticion. Es un DatabaseConfigurer tardio: el
    lifecycle de pico-sqlalchemy garantiza el orden (DDL primero) y desde
    0.5.1 ejecuta los hooks fuera del event loop en cualquier contexto."""

    priority = 100  # despues de SchemaSetup (DDL, prioridad 0)

    def __init__(self, service: RegistryService):
        self._service = service

    def configure_database(self, engine) -> None:
        asyncio.run(self._service.load())


@component
class RegistryHealth:
    """El health del actuator refleja el estado real del catalogo."""

    name = "registry"

    def __init__(self, service: RegistryService):
        self._service = service

    def check(self):
        if not self._service.loaded:
            raise RuntimeError("catalogo no cargado")
        return {"status": "UP", "skills": self._service.size}
