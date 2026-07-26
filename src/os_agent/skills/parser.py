from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..errors import SkillValidationError
from .models import SkillRecord

_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)(.*)\Z", re.DOTALL)
_MANIFEST_NAME = ".os-skill.json"


def _string(value: Any, field: str, *, max_length: int | None = None, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise SkillValidationError(f"SKILL.md frontmatter alanı gerekli: {field}")
        return None
    result = str(value).strip()
    if required and not result:
        raise SkillValidationError(f"SKILL.md frontmatter alanı boş olamaz: {field}")
    if max_length is not None and len(result) > max_length:
        raise SkillValidationError(f"{field} en fazla {max_length} karakter olabilir.")
    return result or None


def parse_skill_directory(root: Path, *, scope: str, max_body_chars: int = 20000) -> SkillRecord:
    skill_file = root / "SKILL.md"
    if not skill_file.is_file():
        raise SkillValidationError(f"SKILL.md bulunamadı: {root}")
    try:
        raw = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillValidationError(f"SKILL.md UTF-8 olarak okunamadı: {exc}") from exc
    match = _FRONTMATTER.match(raw.replace("\r\n", "\n"))
    if not match:
        raise SkillValidationError("SKILL.md YAML frontmatter ile başlamalı ve --- ile kapanmalı.")
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"SKILL.md YAML ayrıştırılamadı: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillValidationError("SKILL.md frontmatter bir YAML nesnesi olmalı.")

    name = _string(frontmatter.get("name"), "name", max_length=64, required=True)
    assert name is not None
    if not _NAME_PATTERN.fullmatch(name):
        raise SkillValidationError("Skill name yalnızca küçük harf, rakam ve tire içermeli; 1-64 karakter olmalı.")
    if root.name.casefold() != name.casefold():
        raise SkillValidationError(f"Skill klasörü adı frontmatter name ile eşleşmeli: {root.name!r} != {name!r}")
    description = _string(frontmatter.get("description"), "description", max_length=1024, required=True)
    assert description is not None
    license_value = _string(frontmatter.get("license"), "license", max_length=256)
    compatibility = _string(frontmatter.get("compatibility"), "compatibility", max_length=500)

    allowed_raw = frontmatter.get("allowed-tools", frontmatter.get("allowed_tools", []))
    if isinstance(allowed_raw, str):
        allowed_tools = tuple(item for item in re.split(r"[\s,]+", allowed_raw.strip()) if item)
    elif isinstance(allowed_raw, list) and all(isinstance(item, str) for item in allowed_raw):
        allowed_tools = tuple(str(item).strip() for item in allowed_raw if str(item).strip())
    elif allowed_raw in (None, []):
        allowed_tools = ()
    else:
        raise SkillValidationError("allowed-tools metin veya metin listesi olmalı.")

    metadata = frontmatter.get("metadata", {}) or {}
    if not isinstance(metadata, dict):
        raise SkillValidationError("metadata bir YAML nesnesi olmalı.")
    metadata = {str(key): value for key, value in metadata.items()}

    body = match.group(2).strip()
    if not body:
        raise SkillValidationError("SKILL.md talimat gövdesi boş olamaz.")
    if len(body) > max_body_chars:
        raise SkillValidationError(f"SKILL.md gövdesi çok büyük: {len(body)} > {max_body_chars} karakter")

    resources: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file() or path.name in {"SKILL.md", _MANIFEST_NAME}:
            continue
        try:
            resources.append(path.relative_to(root).as_posix())
        except ValueError:
            continue

    manifest: dict[str, Any] = {}
    manifest_path = root / _MANIFEST_NAME
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            manifest = {"invalid": True}

    return SkillRecord(
        name=name,
        description=description,
        root=root,
        scope=scope,
        body=body,
        license=license_value,
        compatibility=compatibility,
        allowed_tools=allowed_tools,
        metadata=metadata,
        resources=tuple(resources),
        manifest=manifest,
    )
