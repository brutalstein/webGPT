from __future__ import annotations

from typing import Any

from ...context import ProjectContextEngine
from ...errors import ProjectContextError
from ..models import ToolDefinition, ToolPayload, ToolRisk
from ..registry import Tool, ToolContext, ToolRegistry


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _engine(context: ToolContext) -> ProjectContextEngine:
    engine = context.services.get("project_context")
    if not isinstance(engine, ProjectContextEngine):
        raise ProjectContextError("Proje bağlam motoru kullanılamıyor.")
    return engine


class ProjectContextTool(Tool):
    definition = ToolDefinition(
        name="project_context",
        title="Proje bağlamını oku",
        description=(
            "Seçili projenin dilleri, manifestleri, Git durumu, sembol grafiği, watcher ve indeks sağlığını özetler."
        ),
        input_schema=_schema({}),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        engine = _engine(context)
        return ToolPayload(content=engine.brief(), structured=engine.status())


class SearchProjectContextTool(Tool):
    definition = ToolDefinition(
        name="search_project_context",
        title="Proje bağlamında ara",
        description=(
            "SQLite FTS5, yol/satır boost, oturum çalışma kümesi ve yapısal sinyallerle ilgili kod/doküman parçalarını bulur. "
            "Büyük projelerde kör dosya taraması yerine önce bu aracı kullan."
        ),
        input_schema=_schema(
            {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            ["query"],
        ),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"Proje bağlamında ara: {arguments.get('query')}"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        engine = _engine(context)
        query = str(arguments["query"]).strip()
        limit = min(20, max(1, int(arguments.get("limit", 8))))
        hits = [item.to_wire() for item in engine.search(query, limit=limit, session_id=context.session_id)]
        if not hits:
            return ToolPayload(content="Proje bağlam indeksinde eşleşme bulunamadı.", structured={"query": query, "hits": []})
        lines = [
            f"{item['path']}:{item['line_start']}-{item['line_end']} (score={item['score']})\n{item['text']}"
            for item in hits
        ]
        return ToolPayload(content="\n\n---\n\n".join(lines), structured={"query": query, "hits": hits})


class SearchProjectSymbolsTool(Tool):
    definition = ToolDefinition(
        name="search_project_symbols",
        title="Proje sembollerini ara",
        description=(
            "Tree-sitter/regex yapısal indeksinde sınıf, fonksiyon, metod, tip ve imza arar. "
            "Tanım veya mimari bağlantı sorularında kullan."
        ),
        input_schema=_schema(
            {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
            ["query"],
        ),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"Proje sembollerini ara: {arguments.get('query')}"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        query = str(arguments["query"]).strip()
        limit = min(30, max(1, int(arguments.get("limit", 10))))
        symbols = _engine(context).search_symbols(query, limit=limit)
        if not symbols:
            return ToolPayload(content="Eşleşen proje sembolü bulunamadı.", structured={"query": query, "symbols": []})
        lines = [
            f"{item['path']}:{item['line_start']}-{item['line_end']} · {item['kind']} · "
            f"{item.get('qualified_name') or item.get('name')}\n{item.get('signature', '')}"
            for item in symbols
        ]
        return ToolPayload(content="\n\n".join(lines), structured={"query": query, "symbols": symbols})


class ProjectImpactTool(Tool):
    definition = ToolDefinition(
        name="project_impact",
        title="Değişiklik etkisini incele",
        description=(
            "Bir sembol veya dosyanın tanımlarını, import/call/reference kenarlarını ve ilişkili dosyalarını çıkarır. "
            "Refactor öncesi etki alanını anlamak için kullan."
        ),
        input_schema=_schema(
            {"target": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            ["target"],
        ),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"Etki analizi: {arguments.get('target')}"

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        target = str(arguments["target"]).strip()
        result = _engine(context).impact(target, limit=min(100, max(1, int(arguments.get("limit", 60)))))
        return ToolPayload(
            content=(
                f"Etki analizi: {target}\n"
                f"Tanım: {len(result.get('definitions', []))}, ilişki: {len(result.get('edges', []))}, "
                f"dosya: {len(result.get('related_paths', []))}"
            ),
            structured=result,
        )


class ContextHealthTool(Tool):
    definition = ToolDefinition(
        name="context_health",
        title="Bağlam sağlığını doğrula",
        description="Watcher, arka plan worker, FTS5 deposu, indeks güncelliği ve SQLite bütünlüğünü raporlar.",
        input_schema=_schema({"integrity_check": {"type": "boolean"}}),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        engine = _engine(context)
        status = engine.health(integrity_check=bool(arguments.get("integrity_check", False)))
        return ToolPayload(content=engine.brief(), structured=status)


class RefreshProjectContextTool(Tool):
    definition = ToolDefinition(
        name="refresh_project_context",
        title="Proje bağlam indeksini yenile",
        description="Workspace dosyalarını artımlı olarak yeniden tarar; FTS5, sembol ve ilişki indeksini günceller.",
        input_schema=_schema({"force": {"type": "boolean"}}),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        engine = _engine(context)
        status = engine.refresh(force=bool(arguments.get("force", False)))
        return ToolPayload(
            content=(
                f"Proje bağlamı güncellendi: {status.get('file_count', 0)} dosya, "
                f"{status.get('symbols', 0)} sembol, {status.get('edges', 0)} ilişki."
            ),
            structured=status,
        )


def register_context_tools(registry: ToolRegistry) -> None:
    registry.register(ProjectContextTool())
    registry.register(SearchProjectContextTool())
    registry.register(SearchProjectSymbolsTool())
    registry.register(ProjectImpactTool())
    registry.register(ContextHealthTool())
    registry.register(RefreshProjectContextTool())
