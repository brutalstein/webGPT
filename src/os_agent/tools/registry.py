from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..errors import ToolValidationError
from .models import ToolDefinition, ToolPayload
from .workspace import WorkspaceManager


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace: WorkspaceManager
    settings: dict[str, Any]
    session_id: str


class Tool(ABC):
    definition: ToolDefinition

    @abstractmethod
    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        raise NotImplementedError

    def summarize(self, arguments: dict[str, Any]) -> str:
        return f"{self.definition.title}: {arguments}"


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Araç zaten kayıtlı: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolValidationError(f"Bilinmeyen araç: {name}") from exc

    def definitions(self) -> list[ToolDefinition]:
        return [self._tools[name].definition for name in sorted(self._tools)]

    def manifest(self) -> list[dict[str, Any]]:
        return [definition.to_wire() for definition in self.definitions()]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        tool = self.get(name)
        schema = tool.definition.input_schema
        if not isinstance(arguments, dict):
            raise ToolValidationError(f"{name} arguments nesne olmalı.")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in arguments:
                raise ToolValidationError(f"{name}: zorunlu alan eksik: {key}")
        for key, value in arguments.items():
            definition = properties.get(key)
            if definition is None:
                raise ToolValidationError(f"{name}: desteklenmeyen alan: {key}")
            expected = definition.get("type")
            if expected == "string" and not isinstance(value, str):
                raise ToolValidationError(f"{name}.{key} metin olmalı.")
            if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ToolValidationError(f"{name}.{key} tam sayı olmalı.")
            if expected == "boolean" and not isinstance(value, bool):
                raise ToolValidationError(f"{name}.{key} boolean olmalı.")
            if expected == "array" and not isinstance(value, list):
                raise ToolValidationError(f"{name}.{key} liste olmalı.")
