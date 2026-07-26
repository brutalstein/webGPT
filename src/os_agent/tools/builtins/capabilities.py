from __future__ import annotations

import json
from typing import Any

from ...capabilities import CapabilityManager
from ...errors import CapabilityExecutionError
from ..models import ToolDefinition, ToolPayload, ToolRisk
from ..registry import Tool, ToolContext, ToolRegistry


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _manager(context: ToolContext) -> CapabilityManager:
    manager = context.services.get("capabilities")
    if not isinstance(manager, CapabilityManager):
        raise CapabilityExecutionError("Global capability çalışma zamanı kullanılamıyor.")
    return manager


class ListCapabilitiesTool(Tool):
    definition = ToolDefinition(
        name="list_capabilities",
        title="Global capability kataloğunu listele",
        description=(
            "Klasörden bağımsız olarak OS global extension alanına kurulmuş executable capability'leri, "
            "adapter ve otomasyon durumlarıyla listeler."
        ),
        input_schema=_schema({}),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        payload = _manager(context).status()
        lines = [
            f"- {item['name']} {item['version']} · adapter={item.get('adapter') or 'yok'} · "
            f"auto_start={item.get('auto_start')} · auto_query={item.get('auto_query')}"
            for item in payload["capabilities"]
        ]
        return ToolPayload(content="\n".join(lines) if lines else "Kurulu global capability yok.", structured=payload)


class CapabilityStatusTool(Tool):
    definition = ToolDefinition(
        name="capability_status",
        title="Capability durumunu kontrol et",
        description="Global capability'nin kurulum, otomatik çalışma ve aktif workspace çıktı durumunu döndürür.",
        input_schema=_schema(
            {"name": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"[a-z0-9]+(?:-[a-z0-9]+)*"}},
            ["name"],
        ),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        payload = _manager(context).status(str(arguments["name"]))
        item = payload["capabilities"][0]
        workspace = item.get("workspace") or {}
        content = (
            f"Capability: {item['name']} {item['version']}\n"
            f"Durum: {item['status']} · enabled={item['enabled']} · auto_start={item['auto_start']} · auto_query={item['auto_query']}\n"
            f"Workspace: {workspace.get('status', 'yok')} · ready={workspace.get('ready', False)}\n"
            f"Global output: {workspace.get('output_root') or 'henüz yok'}"
        )
        return ToolPayload(content=content, structured=payload)


class InspectGitHubExtensionTool(Tool):
    definition = ToolDefinition(
        name="inspect_github_extension",
        title="GitHub executable extension kaynağını incele",
        description=(
            "Root SKILL.md zorunlu olmadan public GitHub repository'sini karantinaya indirir; commit, pyproject, "
            "console script, lisans, hash, statik risk ve güvenilen adapter eşleşmesini denetler. Henüz kurmaz."
        ),
        input_schema=_schema(
            {
                "source": {"type": "string", "minLength": 1, "maxLength": 2048},
                "ref": {"type": "string", "maxLength": 256},
            },
            ["source"],
        ),
        risk=ToolRisk.EXECUTE,
        idempotent=False,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"GitHub executable extension'ı indir ve incele: {arguments.get('source')}"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        inspection = _manager(context).inspect_github(
            str(arguments["source"]),
            ref=str(arguments.get("ref", "")).strip() or None,
        )
        report = inspection.report
        package = report.get("package", {})
        risk = report.get("risk", {})
        trusted = bool(report.get("trusted_adapter"))
        next_instruction = (
            "Bu kaynak güvenilen bir adapter ile eşleşti. Kullanıcı global ve otomatik kullanım istediğinde "
            "install_inspected_extension aracını auto_start=true ve auto_query=true ile çağır."
            if trusted
            else
            "Bu kaynak generic Python CLI olarak sınıflandırıldı. Kurulabilir; fakat güvenilen adapter olmadığı için "
            "auto_start ve auto_query false kalmalıdır."
        )
        content = (
            f"Executable extension incelemesi hazır: {report.get('name')}\n"
            f"inspection_id: {inspection.inspection_id}\n"
            f"Sınıf: {report.get('classification')}\n"
            f"Commit: {report.get('source', {}).get('commit')}\n"
            f"Package: {package.get('name')}=={package.get('version')}\n"
            f"Console scripts: {json.dumps(package.get('scripts', {}), ensure_ascii=False)}\n"
            f"Lisans: {package.get('license') or 'belirtilmemiş'}\n"
            f"Adapter: {report.get('adapter') or 'yok'} · trusted={trusted}\n"
            f"Statik bulgu: {len(risk.get('findings', []))}\n"
            f"{next_instruction}\n"
            "Kurulum ayrı kullanıcı onayı gerektirir. Extension ana OS sürecine import edilmeyecek; izole venv/subprocess kullanılacak."
        )
        return ToolPayload(content=content, structured={"inspection_id": inspection.inspection_id, "report": report})


class InstallInspectedExtensionTool(Tool):
    definition = ToolDefinition(
        name="install_inspected_extension",
        title="İncelenen global extension'ı kur",
        description=(
            "Karantinada commit ve hashleri doğrulanmış Python CLI extension'ını OS global packages alanına izole venv ile kurar. "
            "Güvenilen adapter varsa global Agent Skill üretir ve açık izinle arka plan/otomatik sorguyu etkinleştirir."
        ),
        input_schema=_schema(
            {
                "inspection_id": {"type": "string", "pattern": r"[0-9a-f]{32}"},
                "auto_start": {"type": "boolean"},
                "auto_query": {"type": "boolean"},
                "overwrite": {"type": "boolean"},
            },
            ["inspection_id"],
        ),
        risk=ToolRisk.EXECUTE,
        idempotent=False,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return (
            f"Global extension kur: {arguments.get('inspection_id')} · "
            f"auto_start={arguments.get('auto_start', 'adapter varsayılanı')} · "
            f"auto_query={arguments.get('auto_query', 'adapter varsayılanı')}"
        )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        payload = _manager(context).install_inspection(
            str(arguments["inspection_id"]),
            auto_start=arguments.get("auto_start"),
            auto_query=arguments.get("auto_query"),
            overwrite=bool(arguments.get("overwrite", False)),
        )
        return ToolPayload(
            content=(
                f"Global capability kuruldu: {payload['name']} {payload['version']}\n"
                f"İzole kök: {payload['install_root']}\n"
                f"auto_start={payload['auto_start']} · auto_query={payload['auto_query']}\n"
                "Proje çıktıları capability global data alanında tutulacak; çalışma alanına paket kopyalanmayacak."
            ),
            structured=payload,
        )


class QueryCapabilityTool(Tool):
    definition = ToolDefinition(
        name="query_capability",
        title="Global capability'yi salt-okunur sorgula",
        description=(
            "Önceden kurulmuş ve otomatik sorgu izni verilmiş güvenilen capability adapter'ında query, explain, path "
            "veya report işlemi yapar. Proje dosyalarını değiştirmez."
        ),
        input_schema=_schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"[a-z0-9]+(?:-[a-z0-9]+)*"},
                "action": {"type": "string", "minLength": 1, "maxLength": 32},
                "query": {"type": "string", "maxLength": 8000},
                "node": {"type": "string", "maxLength": 1000},
                "source": {"type": "string", "maxLength": 1000},
                "target": {"type": "string", "maxLength": 1000},
            },
            ["name", "action"],
        ),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"{arguments.get('name')} capability sorgusu: {arguments.get('action')}"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        payload = _manager(context).query(str(arguments["name"]), str(arguments["action"]), arguments)
        output = payload.get("stdout", "").strip() or payload.get("stderr", "").strip() or "Capability boş sonuç döndürdü."
        structured = {key: value for key, value in payload.items() if key not in {"stdout", "stderr"}}
        return ToolPayload(content=output, structured=structured)


class RunCapabilityTool(Tool):
    definition = ToolDefinition(
        name="run_capability",
        title="Global capability build/update çalıştır",
        description="Güvenilen global capability'nin proje indeksini açık kullanıcı onayıyla oluşturur veya günceller.",
        input_schema=_schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"[a-z0-9]+(?:-[a-z0-9]+)*"},
                "action": {"type": "string", "pattern": r"build|update"},
            },
            ["name", "action"],
        ),
        risk=ToolRisk.EXECUTE,
        idempotent=False,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"{arguments.get('name')} {arguments.get('action')} işlemini çalıştır"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        payload = _manager(context).run(str(arguments["name"]), str(arguments["action"]))
        return ToolPayload(
            content=(payload.get("stdout", "").strip() or "Capability işlemi tamamlandı."),
            structured={key: value for key, value in payload.items() if key not in {"stdout", "stderr"}},
        )


class ConfigureCapabilityTool(Tool):
    definition = ToolDefinition(
        name="configure_capability",
        title="Global capability politikasını değiştir",
        description="Capability etkinliği ile güvenilen adapter'ın auto_start/auto_query izinlerini kalıcı olarak değiştirir.",
        input_schema=_schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"[a-z0-9]+(?:-[a-z0-9]+)*"},
                "enabled": {"type": "boolean"},
                "auto_start": {"type": "boolean"},
                "auto_query": {"type": "boolean"},
            },
            ["name"],
        ),
        risk=ToolRisk.WRITE,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        payload = _manager(context).configure(
            str(arguments["name"]),
            enabled=arguments.get("enabled"),
            auto_start=arguments.get("auto_start"),
            auto_query=arguments.get("auto_query"),
        )
        return ToolPayload(content=f"Capability politikası güncellendi: {payload['name']}", structured=payload)


class UninstallCapabilityTool(Tool):
    definition = ToolDefinition(
        name="uninstall_capability",
        title="Global capability'yi kaldır",
        description="Global capability paketini ve yönetilen skill kaydını yedekleyerek kaldırır; proje dosyalarına dokunmaz.",
        input_schema=_schema(
            {"name": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"[a-z0-9]+(?:-[a-z0-9]+)*"}},
            ["name"],
        ),
        risk=ToolRisk.WRITE,
        idempotent=False,
        destructive=True,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"Global capability'yi kaldır: {arguments.get('name')}"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        payload = _manager(context).uninstall(str(arguments["name"]))
        return ToolPayload(content=f"Global capability kaldırıldı: {payload['name']}", structured=payload)


def register_capability_tools(registry: ToolRegistry) -> None:
    registry.register(ListCapabilitiesTool())
    registry.register(CapabilityStatusTool())
    registry.register(InspectGitHubExtensionTool())
    registry.register(InstallInspectedExtensionTool())
    registry.register(QueryCapabilityTool())
    registry.register(RunCapabilityTool())
    registry.register(ConfigureCapabilityTool())
    registry.register(UninstallCapabilityTool())
