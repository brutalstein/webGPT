from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}

_DEFINITION_KINDS = {
    "function_definition": "function",
    "function_declaration": "function",
    "function_item": "function",
    "method_definition": "method",
    "method_declaration": "method",
    "method_definition_item": "method",
    "constructor_declaration": "constructor",
    "class_definition": "class",
    "class_declaration": "class",
    "class_specifier": "class",
    "struct_specifier": "struct",
    "struct_item": "struct",
    "interface_declaration": "interface",
    "trait_item": "trait",
    "enum_declaration": "enum",
    "enum_specifier": "enum",
    "enum_item": "enum",
    "type_alias_declaration": "type",
    "type_definition": "type",
    "namespace_definition": "namespace",
    "module": "module",
    "module_declaration": "module",
}

_IMPORT_NODE_TYPES = {
    "import_statement",
    "import_from_statement",
    "import_declaration",
    "include_directive",
    "using_declaration",
    "use_declaration",
    "extern_crate_declaration",
    "package_clause",
}

_CALL_NODE_TYPES = {
    "call",
    "call_expression",
    "function_call",
    "invocation_expression",
    "method_invocation",
    "macro_invocation",
}

_IDENTIFIER_TYPES = {
    "identifier",
    "field_identifier",
    "type_identifier",
    "property_identifier",
    "namespace_identifier",
    "constant",
}

_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|func|fn|struct|enum|interface|trait|type)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([^;\n]+)|#\s*include\s*[<\"]([^>\"]+)|"
    r"(?:use|using)\s+([^;\n]+)|require\s*\(\s*['\"]([^'\"]+)['\"]\s*\))",
    re.MULTILINE,
)
_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")


@dataclass(frozen=True, slots=True)
class StructuralAnalysis:
    backend: str
    language: str | None
    symbols: list[dict[str, Any]]
    imports: list[dict[str, Any]]
    references: list[dict[str, Any]]
    parse_errors: int = 0


class StructuralAnalyzer:
    """Tree-sitter destekli, hata toleranslı kaynak kodu yapı çıkarıcı.

    Parser paketi bulunmazsa güvenli regex fallback'i kullanılır. Parser nesneleri
    thread-safe kabul edilmez; bu yüzden her thread için ayrı parser cache'i tutulur.
    """

    VERSION = 2

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self._local = threading.local()
        self._tree_sitter_available: bool | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("tree_sitter_enabled", True))

    @staticmethod
    def language_for(path: str | Path) -> str | None:
        target = Path(path)
        name = target.name.casefold()
        if name == "dockerfile":
            return "dockerfile"
        if name == "makefile":
            return "make"
        return _LANGUAGE_BY_SUFFIX.get(target.suffix.casefold())

    def _parser(self, language: str):
        if not self.enabled:
            return None
        cache = getattr(self._local, "parsers", None)
        if cache is None:
            cache = {}
            self._local.parsers = cache
        if language in cache:
            return cache[language]
        try:
            from tree_sitter_language_pack import get_parser

            parser = get_parser(language)
        except Exception:
            self._tree_sitter_available = False
            cache[language] = None
            return None
        self._tree_sitter_available = True
        cache[language] = parser
        return parser

    @staticmethod
    def _node_text(source: bytes, node) -> str:
        try:
            return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        except Exception:
            return ""

    @classmethod
    def _name_node(cls, node):
        for field in ("name", "declarator", "type", "function"):
            try:
                candidate = node.child_by_field_name(field)
            except Exception:
                candidate = None
            if candidate is not None:
                if getattr(candidate, "type", "") in _IDENTIFIER_TYPES:
                    return candidate
                nested = cls._first_identifier(candidate)
                if nested is not None:
                    return nested
        return cls._first_identifier(node)

    @staticmethod
    def _first_identifier(node):
        stack = [node]
        seen = 0
        while stack and seen < 80:
            current = stack.pop()
            seen += 1
            if getattr(current, "type", "") in _IDENTIFIER_TYPES:
                return current
            try:
                children = list(current.named_children)
            except Exception:
                children = []
            stack.extend(reversed(children))
        return None

    @staticmethod
    def _clean_reference(value: str) -> str:
        value = value.strip().strip(";,")
        value = value.replace("\n", " ")
        value = re.sub(r"\s+", " ", value)
        if len(value) > 240:
            value = value[:240]
        return value

    @staticmethod
    def _line_text(lines: list[str], start_line: int, end_line: int) -> str:
        if not lines:
            return ""
        start = max(1, start_line) - 1
        end = min(len(lines), max(start + 1, end_line))
        text = " ".join(item.strip() for item in lines[start:end] if item.strip())
        return re.sub(r"\s+", " ", text)[:500]

    def _tree_sitter_analysis(self, path: str, text: str, language: str) -> StructuralAnalysis | None:
        parser = self._parser(language)
        if parser is None:
            return None
        source = text.encode("utf-8", errors="replace")
        try:
            tree = parser.parse(source)
            root = tree.root_node
        except Exception:
            return None

        max_nodes = max(1_000, int(self.settings.get("tree_sitter_max_nodes", 120_000)))
        max_symbols = max(20, int(self.settings.get("max_symbols_per_file", 400)))
        max_refs = max(50, int(self.settings.get("max_references_per_file", 1200)))
        lines = text.splitlines()
        symbols: list[dict[str, Any]] = []
        imports: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        symbol_seen: set[tuple[str, int, str]] = set()
        import_seen: set[tuple[str, int]] = set()
        ref_seen: set[tuple[str, int]] = set()
        parse_errors = 0
        stack = [root]
        visited = 0

        while stack and visited < max_nodes:
            node = stack.pop()
            visited += 1
            node_type = getattr(node, "type", "")
            if node_type == "ERROR" or getattr(node, "is_error", False):
                parse_errors += 1

            kind = _DEFINITION_KINDS.get(node_type)
            if kind and len(symbols) < max_symbols:
                name_node = self._name_node(node)
                name = self._node_text(source, name_node).strip() if name_node is not None else ""
                if name and len(name) <= 200:
                    line_start = int(node.start_point[0]) + 1
                    line_end = int(node.end_point[0]) + 1
                    key = (name, line_start, kind)
                    if key not in symbol_seen:
                        symbol_seen.add(key)
                        symbols.append(
                            {
                                "name": name,
                                "qualified_name": name,
                                "kind": kind,
                                "line_start": line_start,
                                "line_end": line_end,
                                "signature": self._line_text(lines, line_start, min(line_end, line_start + 2)),
                            }
                        )

            if node_type in _IMPORT_NODE_TYPES and len(imports) < max_refs:
                raw = self._clean_reference(self._node_text(source, node))
                line = int(node.start_point[0]) + 1
                for target in self._extract_import_targets(raw):
                    key = (target, line)
                    if key not in import_seen:
                        import_seen.add(key)
                        imports.append({"target": target, "line": line, "raw": raw})

            if node_type in _CALL_NODE_TYPES and len(references) < max_refs:
                target_node = None
                for field in ("function", "name", "method"):
                    try:
                        target_node = node.child_by_field_name(field)
                    except Exception:
                        target_node = None
                    if target_node is not None:
                        break
                if target_node is None:
                    target_node = self._first_identifier(node)
                name = self._clean_reference(self._node_text(source, target_node)) if target_node is not None else ""
                if name:
                    line = int(node.start_point[0]) + 1
                    key = (name, line)
                    if key not in ref_seen:
                        ref_seen.add(key)
                        references.append({"name": name, "line": line, "kind": "call"})

            try:
                children = list(node.named_children)
            except Exception:
                children = []
            stack.extend(reversed(children))

        return StructuralAnalysis(
            backend="tree-sitter",
            language=language,
            symbols=symbols,
            imports=imports,
            references=references,
            parse_errors=parse_errors,
        )

    @staticmethod
    def _extract_import_targets(raw: str) -> list[str]:
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", raw)
        if quoted:
            return [item.strip() for item in quoted if item.strip()]
        cleaned = re.sub(r"^(?:from|import|use|using|package|extern\s+crate)\s+", "", raw).strip()
        cleaned = re.sub(r"\s+import\s+.*$", "", cleaned)
        cleaned = cleaned.strip("<>{}();")
        if not cleaned:
            return []
        parts = [item.strip() for item in re.split(r"\s*,\s*", cleaned)]
        return [item for item in parts if item and len(item) <= 240]

    def _regex_analysis(self, path: str, text: str, language: str | None) -> StructuralAnalysis:
        lines = text.splitlines()
        symbols: list[dict[str, Any]] = []
        for match in _DEF_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            token = match.group(0).lstrip().split(None, 1)[0].casefold()
            kind = {
                "def": "function",
                "function": "function",
                "func": "function",
                "fn": "function",
                "class": "class",
                "struct": "struct",
                "enum": "enum",
                "interface": "interface",
                "trait": "trait",
                "type": "type",
                "async": "function",
            }.get(token, "symbol")
            symbols.append(
                {
                    "name": match.group(1),
                    "qualified_name": match.group(1),
                    "kind": kind,
                    "line_start": line,
                    "line_end": line,
                    "signature": self._line_text(lines, line, line),
                }
            )

        imports: list[dict[str, Any]] = []
        for match in _IMPORT_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            target = next((item for item in match.groups() if item), "")
            for item in self._extract_import_targets(target):
                imports.append({"target": item, "line": line, "raw": match.group(0).strip()[:240]})

        references: list[dict[str, Any]] = []
        definition_names = {item["name"] for item in symbols}
        for match in _CALL_RE.finditer(text):
            name = match.group(1)
            if name in definition_names and match.start() in {item.start() for item in _DEF_RE.finditer(text)}:
                continue
            line = text.count("\n", 0, match.start()) + 1
            references.append({"name": name, "line": line, "kind": "call"})
            if len(references) >= max(50, int(self.settings.get("max_references_per_file", 1200))):
                break

        return StructuralAnalysis(
            backend="regex",
            language=language,
            symbols=symbols[: max(20, int(self.settings.get("max_symbols_per_file", 400)))],
            imports=imports,
            references=references,
            parse_errors=0,
        )

    def analyze(self, path: str, text: str) -> StructuralAnalysis:
        language = self.language_for(path)
        max_bytes = max(4096, int(self.settings.get("structural_max_file_bytes", 524288)))
        if len(text.encode("utf-8", errors="replace")) > max_bytes:
            return StructuralAnalysis("skipped-size", language, [], [], [], 0)
        if language:
            parsed = self._tree_sitter_analysis(path, text, language)
            if parsed is not None:
                return parsed
        return self._regex_analysis(path, text, language)
