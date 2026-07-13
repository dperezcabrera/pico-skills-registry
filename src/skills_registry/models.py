"""Persistent model: skills with immutable versions and an audit trail.

Nothing is ever overwritten: every update inserts a new version row and
moves the skill's current pointer; deprecate/retire are status
transitions recorded in the audit log.
"""

import asyncio
import json

from pico_ioc import component
from pico_sqlalchemy import AppBase, Mapped, mapped_column
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint


class SkillRow(AppBase):
    __tablename__ = "skills"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    superseded_by: Mapped[str] = mapped_column(String(100), default="")
    author: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[int] = mapped_column()


class SkillVersionRow(AppBase):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(20))
    meta_json: Mapped[str] = mapped_column(Text)  # description/triggers/tags/groups/tools
    body: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(String(40))

    def meta(self) -> dict:
        return json.loads(self.meta_json)


class SkillResourceRow(AppBase):
    __tablename__ = "skill_resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("skill_versions.id"), index=True)
    path: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)


class GroupRow(AppBase):
    __tablename__ = "groups"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    can_write: Mapped[bool] = mapped_column(default=False)


class MemberRow(AppBase):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_name", "subject"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_name: Mapped[str] = mapped_column(String(100), index=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)


class AuditRow(AppBase):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(200))
    roles: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(30))
    skill_name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(20), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")


@component
class SchemaSetup:
    """Create tables on startup (swap for Alembic via database.migrations_path)."""

    def configure_database(self, engine) -> None:
        async def _create():
            async with engine.begin() as conn:
                await conn.run_sync(AppBase.metadata.create_all)
            await engine.dispose()

        asyncio.run(_create())
