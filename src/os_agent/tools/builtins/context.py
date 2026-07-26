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
        description="Seçili projenin dilleri, manifestleri, Git durumu, ana klasörleri ve indeks sağlığını özetler.",
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
            "Artımlı proje indeksinde anlamlı kod/doküman parçalarını yol ve satır aralığıyla arar. "
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
        limit = min(20, max(1, int(arguments.get("limit", 6))))
        hits = [item.to_wire() for item in engine.search(query, limit=limit)]
        if not hits:
            return ToolPayload(content="Proje bağlam indeksinde eşleşme bulunamadı.", structured={"query": query, "hits": []})
        lines = [
            f"{item['path']}:{item['line_start']}-{item['line_end']} (score={item['score']})\n{item['text']}"
            for item in hits
        ]
        return ToolPayload(content="\n\n---\n\n".join(lines), structured={"query": query, "hits": hits})


class RefreshProjectContextTool(Tool):
    definition = ToolDefinition(
        name="refresh_project_context",
        title="Proje bağlam indeksini yenile",
        description="Workspace dosyalarını artımlı olarak yeniden tarar ve proje bağlam indeksini günceller.",
        input_schema=_schema({"force": {"type": "boolean"}}),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        engine = _engine(context)
        status = engine.refresh(force=bool(arguments.get("force", False)))
        return ToolPayload(content=f"Proje bağlam indeksi güncellendi: {status.get('file_count', 0)} dosya.", structured=status)


def register_context_tools(registry: ToolRegistry) -> None:
    registry.register(ProjectContextTool())
    registry.register(SearchProjectContextTool())
    registry.register(RefreshProjectContextTool())
