"""The skill contract, independent of storage: payload validation,
canonical content hashing and the frontmatter parser used for imports."""

import hashlib
import json
import re

import yaml

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,98}")
VERSION = re.compile(r"\d+\.\d+\.\d+")
FORBIDDEN = (".env", "id_rsa", ".pem", ".key", ".p12")
MAX_RESOURCE_BYTES = 5 * 1024 * 1024
STATUSES = ("active", "deprecated", "retired")


class ContractError(Exception):
    pass


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def validate_payload(payload: dict) -> dict:
    """Return the normalized skill payload or raise ContractError."""
    name = str(payload.get("name", ""))
    if not NAME.fullmatch(name):
        raise ContractError(f"name invalido '{name}' (kebab-case)")
    if not payload.get("description"):
        raise ContractError(f"{name}: description obligatoria")
    triggers = payload.get("triggers") or []
    if not triggers:
        raise ContractError(f"{name}: al menos un trigger")
    version = str(payload.get("version", ""))
    if not VERSION.fullmatch(version):
        raise ContractError(f"{name}: version obligatoria en formato X.Y.Z")

    resources = payload.get("resources") or {}
    for path, content in resources.items():
        clean = str(path)
        if clean.startswith(("/", "..")) or "/../" in clean:
            raise ContractError(f"{name}: ruta de recurso invalida {clean}")
        lower = clean.lower()
        if any(marker in lower for marker in FORBIDDEN):
            raise ContractError(f"{name}: recurso prohibido {clean} (posible credencial)")
        if len(str(content).encode()) > MAX_RESOURCE_BYTES:
            raise ContractError(f"{name}: recurso {clean} demasiado grande")

    return {
        "name": name,
        "version": version,
        "description": str(payload["description"]),
        "triggers": [str(t) for t in triggers],
        "tags": [str(t) for t in payload.get("tags") or []],
        "groups": [str(g) for g in (payload.get("access") or {}).get("groups") or payload.get("groups") or []],
        "tools": payload.get("tools") or [],
        "body": str(payload.get("body", "")),
        "resources": {str(p): str(c) for p, c in resources.items()},
    }


def content_hash(normalized: dict) -> str:
    canonical = json.dumps(
        {
            k: normalized[k]
            for k in ("name", "version", "description", "triggers", "tags", "groups", "tools", "body", "resources")
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_skill_md(name: str, text: str, resources: dict[str, str]) -> dict:
    """Frontmatter SKILL.md -> payload dict (used by the directory import)."""
    match = FRONTMATTER.match(text)
    if not match:
        raise ContractError(f"{name}: SKILL.md sin frontmatter YAML")
    meta = yaml.safe_load(match.group(1)) or {}
    if meta.get("name") != name:
        raise ContractError(f"{name}: name '{meta.get('name')}' no coincide con el directorio")
    return validate_payload({**meta, "body": match.group(2), "resources": resources})
