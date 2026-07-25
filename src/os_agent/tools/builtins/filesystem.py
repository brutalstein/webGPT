from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from ...errors import ToolError, ToolValidationError
from ..models import ToolDefinition, ToolPayload, ToolRisk
from ..registry import Tool, ToolContext, ToolRegistry


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _is_probably_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\x00" in chunk


def _read_text(path: Path, *, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ToolError(f"Dosya bilgisi okunamadı: {path.name}: {exc}") from exc
    if size > max_bytes:
        raise ToolError(f"Dosya araç sınırından büyük: {size} bayt > {max_bytes} bayt")
    if _is_probably_binary(path):
        raise ToolError(f"İkili dosya metin olarak okunamaz: {path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"Dosya UTF-8 değil: {path.name}") from exc
    except OSError as exc:
        raise ToolError(f"Dosya okunamadı: {path.name}: {exc}") from exc


def _ignored(parts: Iterable[str], ignored_names: set[str]) -> bool:
    return any(part.casefold() in ignored_names for part in parts)


class WorkspaceInfoTool(Tool):
    definition = ToolDefinition(
        name="workspace_info",
        title="Çalışma alanı bilgisi",
        description=(
            "Seçili çalışma alanının mutlak yolunu, üst düzey dosya ve klasörlerini, "
            "Git deposu olup olmadığını ve temel sayaçlarını döndürür. Kullanıcı mevcut "
            "dizini sorarsa önce bu aracı kullan."
        ),
        input_schema=_schema({}),
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        root = context.workspace.require_root()
        ignored = {
            str(item).casefold()
            for item in context.settings.get("ignored_directories", [])
        }
        entries: list[dict[str, Any]] = []
        file_count = 0
        directory_count = 0
        try:
            children = sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        except OSError as exc:
            raise ToolError(f"Çalışma alanı listelenemedi: {exc}") from exc

        for child in children:
            if child.name.casefold() in ignored:
                continue
            kind = "directory" if child.is_dir() else "file"
            if child.is_dir():
                directory_count += 1
            else:
                file_count += 1
            entries.append(
                {
                    "name": child.name,
                    "path": child.name,
                    "type": kind,
                    "size": child.stat().st_size if child.is_file() else None,
                    "symlink": child.is_symlink(),
                }
            )

        visible_limit = max(10, int(context.settings.get("workspace_preview_entries", 80)))
        shown = entries[:visible_limit]
        lines = [
            f"Çalışma alanı: {root}",
            f"Git deposu: {'evet' if (root / '.git').exists() else 'hayır'}",
            f"Üst düzey görünür klasör: {directory_count}",
            f"Üst düzey görünür dosya: {file_count}",
            "İçerik:",
        ]
        lines.extend(
            f"- {'[D]' if item['type'] == 'directory' else '[F]'} {item['name']}"
            for item in shown
        )
        if len(entries) > len(shown):
            lines.append(f"- ... {len(entries) - len(shown)} kayıt daha")
        structured = {
            **context.workspace.describe(),
            "is_git_repository": (root / ".git").exists(),
            "top_level_file_count": file_count,
            "top_level_directory_count": directory_count,
            "entries": shown,
            "truncated": len(entries) > len(shown),
        }
        return ToolPayload(content="\n".join(lines), structured=structured)


class ListDirectoryTool(Tool):
    definition = ToolDefinition(
        name="list_directory",
        title="Klasör listele",
        description=(
            "Çalışma alanı içindeki bir klasörün içeriğini listeler. İsteğe bağlı olarak "
            "sınırlı derinlikte alt klasörlere iner."
        ),
        input_schema=_schema(
            {
                "path": {"type": "string", "description": "Çalışma alanına göre yol; varsayılan ."},
                "depth": {"type": "integer", "description": "0-5 arası alt klasör derinliği"},
                "max_entries": {"type": "integer", "description": "En fazla döndürülecek kayıt"},
            }
        ),
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        path = context.workspace.resolve(arguments.get("path", "."), must_exist=True)
        if not path.is_dir():
            raise ToolValidationError(f"Klasör bekleniyordu: {context.workspace.relative(path)}")
        depth = min(5, max(0, int(arguments.get("depth", 1))))
        max_entries = min(1000, max(1, int(arguments.get("max_entries", 200))))
        ignored = {
            str(item).casefold()
            for item in context.settings.get("ignored_directories", [])
        }
        root_depth = len(path.parts)
        records: list[dict[str, Any]] = []

        for current, directories, files in os.walk(path, followlinks=False):
            current_path = Path(current)
            relative_parts = current_path.relative_to(path).parts
            if _ignored(relative_parts, ignored):
                directories[:] = []
                continue
            current_depth = len(current_path.parts) - root_depth
            directories[:] = sorted(
                [name for name in directories if name.casefold() not in ignored],
                key=str.casefold,
            )
            files = sorted(files, key=str.casefold)
            safe_directories: list[str] = []
            for name in directories:
                child = current_path / name
                try:
                    context.workspace.resolve(child, must_exist=True)
                except ToolError:
                    continue
                display = child.absolute().relative_to(context.workspace.require_root()).as_posix()
                records.append(
                    {
                        "path": display,
                        "type": "directory",
                        "size": None,
                        "symlink": child.is_symlink(),
                    }
                )
                if not child.is_symlink():
                    safe_directories.append(name)
                if len(records) >= max_entries:
                    break
            directories[:] = safe_directories
            if len(records) >= max_entries:
                break
            for name in files:
                child = current_path / name
                try:
                    safe = context.workspace.resolve(child, must_exist=True)
                    size = safe.stat().st_size
                except (OSError, ToolError):
                    continue
                records.append(
                    {
                        "path": context.workspace.relative(safe),
                        "type": "file",
                        "size": size,
                        "symlink": safe.is_symlink(),
                    }
                )
                if len(records) >= max_entries:
                    break
            if len(records) >= max_entries:
                break
            if current_depth >= depth:
                directories[:] = []

        lines = [f"Klasör: {context.workspace.relative(path)}"]
        lines.extend(
            f"- {'[D]' if item['type'] == 'directory' else '[F]'} {item['path']}"
            + (f" ({item['size']} bayt)" if item["size"] is not None else "")
            for item in records
        )
        return ToolPayload(
            content="\n".join(lines),
            structured={
                "path": context.workspace.relative(path),
                "depth": depth,
                "entries": records,
                "truncated": len(records) >= max_entries,
            },
        )


class ReadFileTool(Tool):
    definition = ToolDefinition(
        name="read_file",
        title="Dosya oku",
        description="UTF-8 metin dosyasını satır numaralarıyla okur.",
        input_schema=_schema(
            {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "description": "1 tabanlı başlangıç satırı"},
                "end_line": {"type": "integer", "description": "Dahil son satır"},
            },
            ["path"],
        ),
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        path = context.workspace.resolve(arguments["path"], must_exist=True)
        if not path.is_file():
            raise ToolValidationError(f"Dosya bekleniyordu: {context.workspace.relative(path)}")
        max_bytes = max(1024, int(context.settings.get("max_file_bytes", 1_048_576)))
        text = _read_text(path, max_bytes=max_bytes)
        lines = text.splitlines()
        if lines:
            start = min(len(lines), max(1, int(arguments.get("start_line", 1))))
            end = min(len(lines), max(start, int(arguments.get("end_line", len(lines)))))
            selected = lines[start - 1 : end]
        else:
            start = 0
            end = 0
            selected = []
        numbered = "\n".join(f"{index:>6}: {line}" for index, line in enumerate(selected, start=max(1, start)))
        return ToolPayload(
            content=(
                f"Dosya: {context.workspace.relative(path)}\n"
                f"Satırlar: {start}-{end} / {len(lines)}\n{numbered}"
            ),
            structured={
                "path": context.workspace.relative(path),
                "start_line": start,
                "end_line": end,
                "total_lines": len(lines),
                "content": "\n".join(selected),
            },
        )


class SearchTextTool(Tool):
    definition = ToolDefinition(
        name="search_text",
        title="Metin ara",
        description="Çalışma alanındaki UTF-8 metin dosyalarında sabit metin veya regex arar.",
        input_schema=_schema(
            {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string", "description": "Örnek: *.py veya **/*.md"},
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "max_matches": {"type": "integer"},
            },
            ["query"],
        ),
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        query = str(arguments["query"])
        if not query:
            raise ToolValidationError("Arama metni boş olamaz.")
        base = context.workspace.resolve(arguments.get("path", "."), must_exist=True)
        glob = str(arguments.get("glob", "**/*"))
        use_regex = bool(arguments.get("regex", False))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        max_matches = min(1000, max(1, int(arguments.get("max_matches", 100))))
        ignored = {
            str(item).casefold()
            for item in context.settings.get("ignored_directories", [])
        }
        max_bytes = max(1024, int(context.settings.get("max_file_bytes", 1_048_576)))
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query if use_regex else re.escape(query), flags)
        matches: list[dict[str, Any]] = []
        candidates = [base] if base.is_file() else base.glob(glob)

        for candidate in candidates:
            if len(matches) >= max_matches:
                break
            if not candidate.is_file() or _ignored(candidate.relative_to(base if base.is_dir() else base.parent).parts, ignored):
                continue
            try:
                safe = context.workspace.resolve(candidate, must_exist=True)
                text = _read_text(safe, max_bytes=max_bytes)
            except (ToolError, OSError, ValueError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(
                        {
                            "path": context.workspace.relative(safe),
                            "line": line_number,
                            "text": line[:500],
                        }
                    )
                    if len(matches) >= max_matches:
                        break

        lines = [f"Arama: {query}", f"Eşleşme: {len(matches)}"]
        lines.extend(f"- {item['path']}:{item['line']}: {item['text']}" for item in matches)
        return ToolPayload(
            content="\n".join(lines),
            structured={"query": query, "matches": matches, "truncated": len(matches) >= max_matches},
        )


class WriteFileTool(Tool):
    definition = ToolDefinition(
        name="write_file",
        title="Dosya yaz",
        description="UTF-8 dosyası oluşturur veya açıkça izin verilirse var olan dosyanın tamamını değiştirir.",
        input_schema=_schema(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            ["path", "content"],
        ),
        risk=ToolRisk.WRITE,
        idempotent=True,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"{arguments.get('path')} dosyasına {len(str(arguments.get('content', '')))} karakter yaz"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        path = context.workspace.resolve(arguments["path"], for_write=True)
        content = str(arguments["content"])
        max_bytes = max(1024, int(context.settings.get("max_file_bytes", 1_048_576)))
        if len(content.encode("utf-8")) > max_bytes:
            raise ToolError(f"Yazılacak içerik sınırı aşıyor: {max_bytes} bayt")
        overwrite = bool(arguments.get("overwrite", False))
        if path.exists() and not overwrite:
            raise ToolError("Dosya zaten var. Değiştirmek için overwrite=true gerekli.")
        if path.exists() and not path.is_file():
            raise ToolError("Hedef bir dosya değil.")
        backup = context.workspace.backup_file(path)
        context.workspace.atomic_write(path, content)
        return ToolPayload(
            content=f"Yazıldı: {context.workspace.relative(path)} ({len(content)} karakter)",
            structured={
                "path": context.workspace.relative(path),
                "characters": len(content),
                "backup": str(backup) if backup else None,
            },
        )


class AppendFileTool(Tool):
    definition = ToolDefinition(
        name="append_file",
        title="Dosyaya ekle",
        description="UTF-8 metin dosyasının sonuna içerik ekler; dosya yoksa oluşturur.",
        input_schema=_schema(
            {"path": {"type": "string"}, "content": {"type": "string"}},
            ["path", "content"],
        ),
        risk=ToolRisk.WRITE,
        idempotent=False,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"{arguments.get('path')} dosyasının sonuna {len(str(arguments.get('content', '')))} karakter ekle"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        path = context.workspace.resolve(arguments["path"], for_write=True)
        addition = str(arguments["content"])
        max_bytes = max(1024, int(context.settings.get("max_file_bytes", 1_048_576)))
        existing = ""
        if path.exists():
            if not path.is_file():
                raise ToolError("Hedef bir dosya değil.")
            existing = _read_text(path, max_bytes=max_bytes)
        combined = existing + addition
        if len(combined.encode("utf-8")) > max_bytes:
            raise ToolError(f"Son dosya boyutu sınırı aşıyor: {max_bytes} bayt")
        backup = context.workspace.backup_file(path)
        context.workspace.atomic_write(path, combined)
        return ToolPayload(
            content=f"Eklendi: {context.workspace.relative(path)} ({len(addition)} karakter)",
            structured={
                "path": context.workspace.relative(path),
                "appended_characters": len(addition),
                "backup": str(backup) if backup else None,
            },
        )


class ReplaceTextTool(Tool):
    definition = ToolDefinition(
        name="replace_text",
        title="Dosyada metin değiştir",
        description=(
            "Bir UTF-8 dosyasında old_text değerini new_text ile değiştirir. "
            "Beklenen eşleşme sayısı verilerek yanlış dosya düzenleme riski azaltılabilir."
        ),
        input_schema=_schema(
            {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "expected_replacements": {"type": "integer"},
                "replace_all": {"type": "boolean"},
            },
            ["path", "old_text", "new_text"],
        ),
        risk=ToolRisk.WRITE,
        idempotent=False,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"{arguments.get('path')} içinde doğrulanmış metin değişikliği yap"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        path = context.workspace.resolve(arguments["path"], must_exist=True, for_write=True)
        if not path.is_file():
            raise ToolError("Hedef bir dosya değil.")
        max_bytes = max(1024, int(context.settings.get("max_file_bytes", 1_048_576)))
        original = _read_text(path, max_bytes=max_bytes)
        old = str(arguments["old_text"])
        new = str(arguments["new_text"])
        if not old:
            raise ToolValidationError("old_text boş olamaz.")
        count = original.count(old)
        expected = arguments.get("expected_replacements")
        if expected is not None and count != int(expected):
            raise ToolError(f"Eşleşme sayısı uyuşmuyor: beklenen {expected}, bulunan {count}")
        if count == 0:
            raise ToolError("Değiştirilecek metin dosyada bulunamadı.")
        replace_all = bool(arguments.get("replace_all", False))
        if count > 1 and not replace_all and expected is None:
            raise ToolError(
                f"Metin {count} kez bulundu. replace_all=true veya expected_replacements gerekli."
            )
        limit = -1 if replace_all else 1
        updated = original.replace(old, new, limit)
        if len(updated.encode("utf-8")) > max_bytes:
            raise ToolError(f"Son dosya boyutu sınırı aşıyor: {max_bytes} bayt")
        backup = context.workspace.backup_file(path)
        context.workspace.atomic_write(path, updated)
        replacements = count if replace_all else 1
        return ToolPayload(
            content=f"Değiştirildi: {context.workspace.relative(path)} ({replacements} eşleşme)",
            structured={
                "path": context.workspace.relative(path),
                "replacements": replacements,
                "backup": str(backup) if backup else None,
            },
        )


class CreateDirectoryTool(Tool):
    definition = ToolDefinition(
        name="create_directory",
        title="Klasör oluştur",
        description="Çalışma alanı içinde klasör ve gerekli üst klasörleri oluşturur.",
        input_schema=_schema({"path": {"type": "string"}}, ["path"]),
        risk=ToolRisk.WRITE,
        idempotent=True,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"Klasör oluştur: {arguments.get('path')}"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        path = context.workspace.resolve(arguments["path"], for_write=True)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolError(f"Klasör oluşturulamadı: {exc}") from exc
        return ToolPayload(
            content=f"Klasör hazır: {context.workspace.relative(path)}",
            structured={"path": context.workspace.relative(path)},
        )


def register_filesystem_tools(registry: ToolRegistry) -> None:
    for tool in (
        WorkspaceInfoTool(),
        ListDirectoryTool(),
        ReadFileTool(),
        SearchTextTool(),
        WriteFileTool(),
        AppendFileTool(),
        ReplaceTextTool(),
        CreateDirectoryTool(),
    ):
        registry.register(tool)
