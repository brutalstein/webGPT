from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..errors import ToolValidationError
from .models import ToolDefinition, ToolPayload
from .workspace import WorkspaceManager


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace: WorkspaceManager
    settings: dict[str, Any]
    session_id: str
    services: dict[str, Any] = field(default_factory=dict)


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
            if expected == "array":
                if not isinstance(value, list):
                    raise ToolValidationError(f"{name}.{key} liste olmalı.")
                item_schema = definition.get("items", {})
                if item_schema.get("type") == "string" and not all(isinstance(item, str) for item in value):
                    raise ToolValidationError(f"{name}.{key} yalnızca metin elemanları içermeli.")
                if "minItems" in definition and len(value) < int(definition["minItems"]):
                    raise ToolValidationError(f"{name}.{key} en az {definition['minItems']} eleman içermeli.")
                if "maxItems" in definition and len(value) > int(definition["maxItems"]):
                    raise ToolValidationError(f"{name}.{key} en fazla {definition['maxItems']} eleman içermeli.")
            if expected == "string" and isinstance(value, str):
                if "minLength" in definition and len(value) < int(definition["minLength"]):
                    raise ToolValidationError(f"{name}.{key} en az {definition['minLength']} karakter olmalı.")
                if "maxLength" in definition and len(value) > int(definition["maxLength"]):
                    raise ToolValidationError(f"{name}.{key} en fazla {definition['maxLength']} karakter olmalı.")
                pattern = definition.get("pattern")
                if pattern:
                    import re
                    if re.fullmatch(str(pattern), value) is None:
                        raise ToolValidationError(f"{name}.{key} beklenen biçime uymuyor.")
            if expected == "integer" and isinstance(value, int) and not isinstance(value, bool):
                if "minimum" in definition and value < int(definition["minimum"]):
                    raise ToolValidationError(f"{name}.{key} en az {definition['minimum']} olmalı.")
                if "maximum" in definition and value > int(definition["maximum"]):
                    raise ToolValidationError(f"{name}.{key} en fazla {definition['maximum']} olmalı.")
