"""Directory catalog validator and offline search.

This is the piece catalog repositories use in CI (contract check plus
golden-set eval) and the seed import parses through. The running
registry itself stores skills in its backend (see store.py); a directory
is input, not the source of truth.
"""

import re
import sqlite3
from pathlib import Path

from .contract import ContractError, content_hash, parse_skill_md
from .store import SkillView

CatalogError = ContractError


def load_dir(root: Path) -> dict[str, SkillView]:
    skills_dir = Path(root) / "skills"
    if not skills_dir.is_dir():
        raise ContractError(f"catalogo invalido: no existe {skills_dir}")
    skills: dict[str, SkillView] = {}
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        resources = {
            str(f.relative_to(skill_dir)): f.read_text(encoding="utf-8")
            for f in sorted(skill_dir.rglob("*"))
            if f.is_file() and f.name != "SKILL.md"
        }
        norm = parse_skill_md(skill_dir.name, (skill_dir / "SKILL.md").read_text(encoding="utf-8"), resources)
        meta = norm  # frontmatter opcionalmente trae status/superseded_by
        raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        status = (
            "deprecated" if "\nstatus: deprecated" in raw else ("retired" if "\nstatus: retired" in raw else "active")
        )
        skills[norm["name"]] = SkillView(
            name=norm["name"],
            version=norm["version"],
            status=status,
            superseded_by="",
            author="catalog",
            description=meta["description"],
            triggers=meta["triggers"],
            tags=meta["tags"],
            groups=meta["groups"],
            tools=meta["tools"],
            body=norm["body"],
            sha256=content_hash(norm),
            resources=sorted(norm["resources"]),
        )
    if not skills:
        raise ContractError("catalogo vacio")
    return skills


class Catalog:
    """Compatibility surface for catalog CI: .skills plus .search()."""

    def __init__(self, root: Path):
        self.skills = load_dir(root)
        self._index = sqlite3.connect(":memory:", check_same_thread=False)
        self._index.execute("CREATE VIRTUAL TABLE skills USING fts5(name, description, triggers, tags)")
        self._index.executemany(
            "INSERT INTO skills VALUES (?, ?, ?, ?)",
            [(s.name, s.description, " ".join(s.triggers), " ".join(s.tags)) for s in self.skills.values()],
        )

    def search(self, query: str, roles: set[str], limit: int = 10) -> list[SkillView]:
        sanitized = " ".join(re.findall(r"\w+", query))
        if not sanitized:
            return []
        rows = self._index.execute(
            "SELECT name FROM skills WHERE skills MATCH ? ORDER BY bm25(skills) LIMIT ?",
            (" OR ".join(sanitized.split()), limit * 3),
        ).fetchall()
        hits = [self.skills[name] for (name,) in rows if self.skills[name].visible_to(roles)]
        hits.sort(key=lambda s: s.status != "active")
        return hits[:limit]
