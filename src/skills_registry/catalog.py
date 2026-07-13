"""Catalog loading: contract validation, content hashing and FTS search.

A catalog is a directory (usually a mounted volume backed by a protected
git repository) with one subdirectory per skill:

    skills/<name>/SKILL.md      # YAML frontmatter + markdown body
    skills/<name>/resources/*   # optional scripts and files

The registry never executes anything from the catalog: it validates,
hashes, indexes and serves.
"""

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
FORBIDDEN = (".env", "id_rsa", ".pem", ".key", ".p12")
MAX_RESOURCE_BYTES = 5 * 1024 * 1024


class CatalogError(Exception):
    pass


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    tags: list[str]
    groups: list[str]  # empty = visible to any authenticated caller
    tools: list[dict]
    body: str
    sha256: str
    path: Path
    resources: list[str] = field(default_factory=list)

    def visible_to(self, roles: set[str]) -> bool:
        return not self.groups or "admin" in roles or bool(roles & set(self.groups))

    def meta(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "tags": self.tags,
            "groups": self.groups,
            "tools": self.tools,
            "sha256": self.sha256,
            "resources": self.resources,
        }


def _parse_skill(skill_dir: Path) -> Skill:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        raise CatalogError(f"{skill_dir.name}: falta SKILL.md")
    match = FRONTMATTER.match(md.read_text(encoding="utf-8"))
    if not match:
        raise CatalogError(f"{skill_dir.name}: SKILL.md sin frontmatter YAML")
    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)

    name = meta.get("name", "")
    if name != skill_dir.name:
        raise CatalogError(f"{skill_dir.name}: name '{name}' no coincide con el directorio")
    if not meta.get("description"):
        raise CatalogError(f"{name}: description obligatoria")
    triggers = meta.get("triggers") or []
    if not triggers:
        raise CatalogError(f"{name}: al menos un trigger")

    resources = []
    hasher = hashlib.sha256()
    for f in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        lower = f.name.lower()
        if any(marker in lower for marker in FORBIDDEN):
            raise CatalogError(f"{name}: recurso prohibido {f.name} (posible credencial)")
        if f.stat().st_size > MAX_RESOURCE_BYTES:
            raise CatalogError(f"{name}: recurso {f.name} demasiado grande")
        hasher.update(str(f.relative_to(skill_dir)).encode())
        hasher.update(f.read_bytes())
        if f != md:
            resources.append(str(f.relative_to(skill_dir)))

    access = meta.get("access") or {}
    return Skill(
        name=name,
        description=str(meta["description"]),
        triggers=[str(t) for t in triggers],
        tags=[str(t) for t in meta.get("tags") or []],
        groups=[str(g) for g in access.get("groups") or []],
        tools=meta.get("tools") or [],
        body=body,
        sha256=hasher.hexdigest(),
        path=skill_dir,
        resources=resources,
    )


class Catalog:
    """In-memory FTS5 index over the validated skills of one directory."""

    def __init__(self, root: Path):
        skills_dir = root / "skills"
        if not skills_dir.is_dir():
            raise CatalogError(f"catalogo invalido: no existe {skills_dir}")
        self.skills: dict[str, Skill] = {}
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill = _parse_skill(skill_dir)  # fail-fast: un catalogo invalido no arranca
            self.skills[skill.name] = skill
        if not self.skills:
            raise CatalogError("catalogo vacio")

        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.execute("CREATE VIRTUAL TABLE skills USING fts5(name, description, triggers, tags)")
        self._db.executemany(
            "INSERT INTO skills VALUES (?, ?, ?, ?)",
            [(s.name, s.description, " ".join(s.triggers), " ".join(s.tags)) for s in self.skills.values()],
        )
        logger.info("catalogo cargado: %d skills", len(self.skills))

    def search(self, query: str, roles: set[str], limit: int = 10) -> list[Skill]:
        sanitized = " ".join(re.findall(r"\w+", query))
        if not sanitized:
            return []
        rows = self._db.execute(
            "SELECT name FROM skills WHERE skills MATCH ? ORDER BY bm25(skills) LIMIT ?",
            (" OR ".join(sanitized.split()), limit * 3),
        ).fetchall()
        hits = (self.skills[name] for (name,) in rows)
        return [s for s in hits if s.visible_to(roles)][:limit]

    def visible(self, roles: set[str]) -> list[Skill]:
        return [s for s in self.skills.values() if s.visible_to(roles)]

    def resource_path(self, skill: Skill, relative: str) -> Path:
        candidate = (skill.path / relative).resolve()
        if not candidate.is_relative_to(skill.path.resolve()) or not candidate.is_file():
            raise CatalogError(f"recurso no encontrado: {relative}")
        return candidate
