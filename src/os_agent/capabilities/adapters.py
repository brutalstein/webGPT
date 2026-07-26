from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..errors import CapabilityExecutionError
from .models import CapabilityRecord

_PROJECT_TERMS = (
    "proje", "project", "kod", "code", "mimari", "architecture", "bağımlılık",
    "dependency", "çağrı", "call", "akış", "flow", "modül", "module", "sınıf",
    "class", "fonksiyon", "function", "servis", "service", "repo", "repository",
    "nasıl çalış", "how does", "etki", "impact", "ilişki", "relationship",
)


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    name: str
    repositories: tuple[str, ...]
    package_names: tuple[str, ...]
    module: str
    display_name: str
    trusted: bool
    default_auto_start: bool
    default_auto_query: bool


class CapabilityAdapter:
    descriptor: AdapterDescriptor

    def matches(self, source: dict[str, Any], package: dict[str, Any]) -> bool:
        repo = f"{source.get('owner', '')}/{source.get('repo', '')}".casefold()
        package_name = str(package.get("name", "")).casefold()
        repositories = {item.casefold() for item in self.descriptor.repositories}
        packages = {item.casefold() for item in self.descriptor.package_names}
        # Trusted adapter kimliği yalnız package adına dayanamaz; aynı PyPI adını
        # kullanan başka bir GitHub deposu otomatik yetki kazanmamalıdır.
        if repositories and repo not in repositories:
            return False
        return not packages or package_name in packages

    def workspace_key(self, root: Path) -> str:
        return hashlib.sha256(str(root.resolve()).casefold().encode("utf-8", errors="replace")).hexdigest()[:24]

    def output_root(self, data_root: Path, workspace: Path) -> Path:
        return data_root / self.descriptor.name / self.workspace_key(workspace)

    def graph_ready(self, output_root: Path) -> bool:
        return False

    def is_relevant(self, prompt: str) -> bool:
        folded = " ".join(prompt.casefold().split())
        return any(term in folded for term in _PROJECT_TERMS)

    def command(
        self,
        record: CapabilityRecord,
        action: str,
        workspace: Path,
        output_root: Path,
        arguments: dict[str, Any],
    ) -> tuple[list[str], dict[str, str], bool]:
        raise CapabilityExecutionError(f"Capability adapter bu işlemi desteklemiyor: {action}")

    def generated_skill(self, record: CapabilityRecord) -> tuple[str, dict[str, str]]:
        raise CapabilityExecutionError("Bu capability için otomatik skill şablonu yok.")


class GraphifyAdapter(CapabilityAdapter):
    descriptor = AdapterDescriptor(
        name="graphify",
        repositories=("Graphify-Labs/graphify",),
        package_names=("graphifyy",),
        module="graphify",
        display_name="Graphify knowledge graph",
        trusted=True,
        default_auto_start=True,
        default_auto_query=True,
    )

    def output_root(self, data_root: Path, workspace: Path) -> Path:
        return super().output_root(data_root, workspace) / "graphify-out"

    def graph_ready(self, output_root: Path) -> bool:
        return (output_root / "graph.json").is_file()

    def is_relevant(self, prompt: str) -> bool:
        folded = " ".join(prompt.casefold().split())
        if "graphify" in folded or "knowledge graph" in folded or "bilgi graf" in folded:
            return True
        return super().is_relevant(prompt)

    def command(
        self,
        record: CapabilityRecord,
        action: str,
        workspace: Path,
        output_root: Path,
        arguments: dict[str, Any],
    ) -> tuple[list[str], dict[str, str], bool]:
        python = str(record.python_executable)
        env = {
            "GRAPHIFY_OUT": str(output_root),
            "GRAPHIFY_NO_TELEMETRY": "1",
        }
        action = action.casefold().strip()
        if action == "build":
            command = [python, "-m", record.module, str(workspace), "--no-viz"]
        elif action == "update":
            command = [python, "-m", record.module, str(workspace), "--update", "--no-viz"]
        elif action == "query":
            query = " ".join(str(arguments.get("query", "")).split())
            if not query:
                raise CapabilityExecutionError("Graphify query için soru gerekli.")
            command = [python, "-m", record.module, "query", query]
        elif action == "explain":
            node = " ".join(str(arguments.get("node", "")).split())
            if not node:
                raise CapabilityExecutionError("Graphify explain için node gerekli.")
            command = [python, "-m", record.module, "explain", node]
        elif action == "path":
            source = " ".join(str(arguments.get("source", "")).split())
            target = " ".join(str(arguments.get("target", "")).split())
            if not source or not target:
                raise CapabilityExecutionError("Graphify path için source ve target gerekli.")
            command = [python, "-m", record.module, "path", source, target]
        elif action == "report":
            report = output_root / "GRAPH_REPORT.md"
            if not report.is_file():
                raise CapabilityExecutionError("Graphify raporu henüz oluşturulmadı.")
            return [python, "-c", "from pathlib import Path; print(Path(__import__('sys').argv[1]).read_text(encoding='utf-8'))", str(report)], env, False
        else:
            raise CapabilityExecutionError(f"Graphify işlemi desteklenmiyor: {action}")
        # Yerel proje yolu kullanılır; network gerektiren GitHub URL akışları adapter tarafından açılmaz.
        return command, env, False

    def generated_skill(self, record: CapabilityRecord) -> tuple[str, dict[str, str]]:
        skill = f"""---
name: graphify-global
description: Proje mimarisi, bağımlılıklar, çağrı akışları, sembol ilişkileri ve kod tabanı sorularında global Graphify bilgi grafını otomatik kullan. Graph hazırsa grep yerine önce query_capability ile graphify sorgusu yap; ayrıntıları yerel dosya araçlarıyla doğrula.
license: Apache-2.0
compatibility: OS global capability runtime
allowed-tools:
  - capability_status
  - query_capability
  - run_capability
  - search_project_context
  - read_file
metadata:
  capability: graphify
  package: graphifyy
  version: {record.version}
  auto-start: {str(record.auto_start).lower()}
  auto-query: {str(record.auto_query).lower()}
---

# Graphify Global Capability

Bu skill, `%LOCALAPPDATA%\\OS\\extensions` altında izole kurulu Graphify capability'sini yönlendirir. Proje içine paket veya skill kopyalama.

## Zorunlu kullanım sırası

1. Proje mimarisi, çağrı akışı, bağımlılık veya ilişki sorularında önce `query_capability` aracını `name=graphify`, `action=query` ile kullan.
2. Graph henüz hazır değilse `capability_status` ile durumu bildir. Arka plan build devam eder; aynı build'i tekrar başlatma.
3. Kullanıcı açıkça yeniden oluşturma veya güncelleme isterse `run_capability` kullan. Bu işlem kullanıcı onayına tabidir.
4. Graph sonucu çıkarımdır. Kritik değişikliklerden önce `read_file`, `search_project_context` ve testlerle doğrula.
5. Capability çıktıları proje içine değil, OS global data alanına yazılır.

## Desteklenen işlemler

- `query`: doğal dil sorusuna ilgili alt grafı getirir.
- `explain`: bir node'u açıklar.
- `path`: iki kavram arasındaki yolu bulur.
- `report`: son `GRAPH_REPORT.md` içeriğini okur.
- `build` / `update`: bilgi grafını oluşturur veya yeniler.

Capability sürecinin API anahtarlarına erişimi yoktur ve normal çalışma sırasında HTTP proxy kara deliği uygulanır. Bu sınır kernel seviyesinde tam sandbox değildir; OS kullanıcı izinleri geçerlidir.
"""
        refs = {
            "references/runtime.md": (
                "# Runtime provenance\n\n"
                f"- Source: {record.source.get('web_url') or record.source}\n"
                f"- Commit: `{record.commit}`\n"
                f"- Package: `graphifyy=={record.version}`\n"
                f"- Isolated root: `{record.install_root}`\n"
            )
        }
        return skill, refs


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, CapabilityAdapter] = {"graphify": GraphifyAdapter()}

    def detect(self, source: dict[str, Any], package: dict[str, Any]) -> CapabilityAdapter | None:
        for adapter in self._adapters.values():
            if adapter.matches(source, package):
                return adapter
        return None

    def get(self, name: str | None) -> CapabilityAdapter | None:
        if not name:
            return None
        return self._adapters.get(name.casefold().strip())

    def descriptors(self) -> list[dict[str, Any]]:
        return [asdict(adapter.descriptor) for adapter in self._adapters.values()]


def normalize_capability_name(value: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not folded or len(folded) > 64:
        raise CapabilityExecutionError("Capability adı güvenli slug biçimine dönüştürülemedi.")
    return folded
