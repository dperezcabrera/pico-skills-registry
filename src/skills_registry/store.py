"""Storage port and the SQL driver.

The backend is a conscious, configurable decision: ``SkillStore`` is the
port, drivers are components selected by ``registry.backend``. Every
driver must pass the shared contract suite (tests/test_store_contract.py).
Available: ``db`` (this module). Planned: ``git``.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pico_ioc import component
from pico_sqlalchemy import SessionManager, get_session, transactional
from sqlalchemy import select

from .models import AuditRow, GroupRow, MemberRow, SkillResourceRow, SkillRow, SkillVersionRow


class StoreError(Exception):
    pass


class SkillExists(StoreError):
    pass


class SkillNotFound(StoreError):
    pass


class VersionNotBumped(StoreError):
    pass


@dataclass
class SkillView:
    name: str
    version: str
    status: str
    superseded_by: str
    author: str
    description: str
    triggers: list[str]
    tags: list[str]
    groups: list[str]
    tools: list[dict]
    body: str
    sha256: str
    resources: list[str] = field(default_factory=list)

    def visible_to(self, roles: set[str]) -> bool:
        if self.status == "retired":
            return False
        return not self.groups or "admin" in roles or bool(roles & set(self.groups))

    def meta(self) -> dict:
        meta = {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "author": self.author,
            "description": self.description,
            "triggers": self.triggers,
            "tags": self.tags,
            "groups": self.groups,
            "tools": self.tools,
            "sha256": self.sha256,
            "resources": self.resources,
        }
        if self.superseded_by:
            meta["superseded_by"] = self.superseded_by
        return meta


@runtime_checkable
class SkillStore(Protocol):
    """Port: immutable versions, status transitions, per-mutation audit."""

    async def load_all(self) -> list[SkillView]: ...

    async def create(self, subject: str, roles: str, norm: dict, sha256: str) -> SkillView: ...

    async def update(self, subject: str, roles: str, norm: dict, sha256: str) -> SkillView: ...

    async def transition(self, subject: str, roles: str, name: str, status: str, superseded_by: str) -> SkillView: ...

    async def resource(self, name: str, path: str) -> str | None: ...

    async def versions(self, name: str) -> list[dict]: ...

    async def audit(self, limit: int) -> list[dict]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _view(skill: SkillRow, version: SkillVersionRow, resources: list[str]) -> SkillView:
    meta = version.meta()
    return SkillView(
        name=skill.name,
        version=version.version,
        status=skill.status,
        superseded_by=skill.superseded_by,
        author=skill.author,
        description=meta["description"],
        triggers=meta["triggers"],
        tags=meta["tags"],
        groups=meta["groups"],
        tools=meta["tools"],
        body=version.body,
        sha256=version.sha256,
        resources=resources,
    )


@component
class SqlSkillStore:
    def __init__(self, session_manager: SessionManager):
        self.sm = session_manager

    async def _current(self, session, name: str) -> tuple[SkillRow, SkillVersionRow, list[str]]:
        skill = await session.get(SkillRow, name)
        if skill is None:
            raise SkillNotFound(name)
        version = await session.get(SkillVersionRow, skill.current_version_id)
        paths = (
            (await session.execute(select(SkillResourceRow.path).where(SkillResourceRow.version_id == version.id)))
            .scalars()
            .all()
        )
        return skill, version, list(paths)

    async def _insert_version(self, session, subject: str, norm: dict, sha256: str) -> SkillVersionRow:
        version = SkillVersionRow(
            skill_name=norm["name"],
            version=norm["version"],
            meta_json=json.dumps(
                {k: norm[k] for k in ("description", "triggers", "tags", "groups", "tools")}, ensure_ascii=False
            ),
            body=norm["body"],
            sha256=sha256,
            created_by=subject,
            created_at=_now(),
        )
        session.add(version)
        await session.flush()
        for path, content in norm["resources"].items():
            session.add(SkillResourceRow(version_id=version.id, path=path, content=content))
        return version

    def _audit(self, session, subject: str, roles: str, action: str, name: str, version: str = "", sha256: str = ""):
        session.add(
            AuditRow(
                at=_now(), subject=subject, roles=roles, action=action, skill_name=name, version=version, sha256=sha256
            )
        )

    @transactional
    async def load_all(self) -> list[SkillView]:
        session = get_session(self.sm)
        views = []
        for skill in (await session.execute(select(SkillRow))).scalars().all():
            _, version, paths = await self._current(session, skill.name)
            views.append(_view(skill, version, paths))
        return views

    @transactional
    async def create(self, subject: str, roles: str, norm: dict, sha256: str) -> SkillView:
        session = get_session(self.sm)
        if await session.get(SkillRow, norm["name"]) is not None:
            raise SkillExists(norm["name"])
        version = await self._insert_version(session, subject, norm, sha256)
        skill = SkillRow(name=norm["name"], status="active", author=subject, current_version_id=version.id)
        session.add(skill)
        self._audit(session, subject, roles, "create", norm["name"], norm["version"], sha256)
        await session.flush()
        return _view(skill, version, list(norm["resources"]))

    @transactional
    async def update(self, subject: str, roles: str, norm: dict, sha256: str) -> SkillView:
        from .contract import version_tuple

        session = get_session(self.sm)
        skill, current, _ = await self._current(session, norm["name"])
        if version_tuple(norm["version"]) <= version_tuple(current.version):
            raise VersionNotBumped(f"{norm['version']} <= {current.version}")
        version = await self._insert_version(session, subject, norm, sha256)
        skill.current_version_id = version.id
        self._audit(session, subject, roles, "update", norm["name"], norm["version"], sha256)
        await session.flush()
        return _view(skill, version, list(norm["resources"]))

    @transactional
    async def transition(self, subject: str, roles: str, name: str, status: str, superseded_by: str) -> SkillView:
        session = get_session(self.sm)
        skill, version, paths = await self._current(session, name)
        skill.status = status
        skill.superseded_by = superseded_by
        self._audit(session, subject, roles, status, name, version.version, version.sha256)
        await session.flush()
        return _view(skill, version, paths)

    @transactional
    async def resource(self, name: str, path: str) -> str | None:
        session = get_session(self.sm)
        skill, version, _ = await self._current(session, name)
        row = (
            await session.execute(
                select(SkillResourceRow).where(SkillResourceRow.version_id == version.id, SkillResourceRow.path == path)
            )
        ).scalar_one_or_none()
        return None if row is None else row.content

    @transactional
    async def versions(self, name: str) -> list[dict]:
        session = get_session(self.sm)
        rows = (
            (
                await session.execute(
                    select(SkillVersionRow).where(SkillVersionRow.skill_name == name).order_by(SkillVersionRow.id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            raise SkillNotFound(name)
        return [
            {"version": r.version, "sha256": r.sha256, "created_by": r.created_by, "created_at": r.created_at}
            for r in rows
        ]

    @transactional
    async def audit(self, limit: int) -> list[dict]:
        session = get_session(self.sm)
        rows = (await session.execute(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit))).scalars().all()
        return [
            {
                "at": r.at,
                "subject": r.subject,
                "roles": r.roles,
                "action": r.action,
                "skill": r.skill_name,
                "version": r.version,
                "sha256": r.sha256,
            }
            for r in rows
        ]

    # ── grupos y membresias (RBAC) ───────────────────────────────

    @transactional
    async def groups(self) -> list[dict]:
        session = get_session(self.sm)
        groups = (await session.execute(select(GroupRow))).scalars().all()
        members = (await session.execute(select(MemberRow))).scalars().all()
        by_group: dict[str, list[str]] = {}
        for m in members:
            by_group.setdefault(m.group_name, []).append(m.subject)
        return [{"name": g.name, "can_write": g.can_write, "members": sorted(by_group.get(g.name, []))} for g in groups]

    @transactional
    async def upsert_group(self, subject: str, roles: str, name: str, can_write: bool) -> dict:
        session = get_session(self.sm)
        group = await session.get(GroupRow, name)
        if group is None:
            group = GroupRow(name=name, can_write=can_write)
            session.add(group)
        else:
            group.can_write = can_write
        self._audit(session, subject, roles, "group-upsert", name, version="write" if can_write else "read")
        await session.flush()
        return {"name": name, "can_write": can_write}

    @transactional
    async def set_membership(self, subject: str, roles: str, group: str, member: str, add: bool) -> None:
        session = get_session(self.sm)
        if await session.get(GroupRow, group) is None:
            raise SkillNotFound(f"grupo {group}")
        existing = (
            await session.execute(select(MemberRow).where(MemberRow.group_name == group, MemberRow.subject == member))
        ).scalar_one_or_none()
        if add and existing is None:
            session.add(MemberRow(group_name=group, subject=member))
        if not add and existing is not None:
            await session.delete(existing)
        self._audit(session, subject, roles, "member-add" if add else "member-remove", group, version=member)
        await session.flush()

    @transactional
    async def rbac_snapshot(self) -> tuple[dict[str, bool], dict[str, set[str]]]:
        """(grupo -> can_write, subject -> grupos): para cachear en memoria."""
        session = get_session(self.sm)
        writable = {g.name: g.can_write for g in (await session.execute(select(GroupRow))).scalars().all()}
        memberships: dict[str, set[str]] = {}
        for m in (await session.execute(select(MemberRow))).scalars().all():
            memberships.setdefault(m.subject, set()).add(m.group_name)
        return writable, memberships
