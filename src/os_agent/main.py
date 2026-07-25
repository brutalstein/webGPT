from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .config import AppConfig, load_config
from .core.memory_store import MemoryStore
from .core.provider_registry import ProviderRegistry
from .core.session_store import SessionStore
from .core.storage import StateDatabase
from .errors import OSErrorBase
from .providers.chatgpt_api import OpenAIResponsesProvider
from .providers.gemini_web import GeminiWebProvider
from .tools import LocalToolRuntime
from .ui.app import TerminalApplication
from .ui.workspace_picker import choose_workspace

ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OS kişisel AI terminali")
    parser.add_argument("--setup", action="store_true", help="Google hesabı ve Gemini kurulumunu açar")
    parser.add_argument("--doctor", action="store_true", help="Gemini sistem tanısı üretir")
    parser.add_argument("--repair", action="store_true", help="Gemini profilini oturumu silmeden onarır")
    parser.add_argument("--backup", action="store_true", help="Durum veritabanını hemen yedekler")
    parser.add_argument("--visible", action="store_true", help="Gemini Chrome'u görünür hata ayıklama modunda açar")
    parser.add_argument("--setup-openai", action="store_true", help="OpenAI API anahtarını güvenli kasaya kaydeder")
    parser.add_argument("--workspace", metavar="KLASÖR", help="Bu çalıştırma için yerel çalışma alanını seçer ve kaydeder")
    parser.add_argument("--select-workspace", action="store_true", help="Grafik klasör seçiciyle çalışma alanını belirler")
    parser.add_argument("--workspace-info", action="store_true", help="Kayıtlı çalışma alanı ve araç durumunu gösterir")
    return parser.parse_args()


def build_registry(config: AppConfig, tool_runtime: LocalToolRuntime) -> ProviderRegistry:
    registry = ProviderRegistry(config)
    registry.register(
        "gemini_chrome_cdp",
        lambda app, settings: GeminiWebProvider(app, settings, tool_runtime=tool_runtime),
    )
    registry.register("openai_responses_api", lambda app, settings: OpenAIResponsesProvider(app, settings))
    return registry


def prepare_storage(config: AppConfig) -> tuple[StateDatabase, SessionStore, MemoryStore]:
    database_preexisted = config.database_path.exists()
    database = StateDatabase(config.database_path)

    if database_preexisted and bool(config.storage.get("automatic_backup", True)):
        database.backup_if_due(
            config.backups_dir,
            interval_hours=max(1, int(config.storage.get("backup_interval_hours", 24))),
            keep=max(1, int(config.storage.get("backup_keep", 10))),
        )

    imported_sessions = database.import_legacy_sessions(config.sessions_path)
    imported_memory = database.import_legacy_memory(config.memory_path)
    if imported_sessions or imported_memory:
        database.copy_legacy_files(
            [config.sessions_path, config.memory_path],
            config.backups_dir / "legacy-json",
        )

    health = database.quick_check()
    if health.casefold() != "ok":
        from .errors import StorageError

        raise StorageError(f"SQLite bütünlük kontrolü başarısız: {health}")

    return database, SessionStore(database), MemoryStore(database)


def run_direct_action(
    args: argparse.Namespace,
    config: AppConfig,
    registry: ProviderRegistry,
    database: StateDatabase,
    console: Console,
) -> int | None:
    provider = registry.get("gemini")
    if args.setup_openai:
        registry.get("chatgpt").setup()
        return 0
    if args.setup:
        provider.setup()
        return 0
    if args.doctor:
        report = provider.doctor.run()
        console.print(f"[green]Rapor oluşturuldu:[/green] {report}")
        return 0
    if args.repair:
        provider.doctor.soft_repair()
        console.print("[green]Yumuşak onarım tamamlandı.[/green]")
        return 0
    if args.backup:
        path = database.backup_now(
            config.backups_dir,
            keep=max(1, int(config.storage.get("backup_keep", 10))),
        )
        console.print(f"[green]Yedek oluşturuldu:[/green] {path}")
        return 0
    return None


def configure_workspace(
    args: argparse.Namespace,
    runtime: LocalToolRuntime,
    console: Console,
) -> int | None:
    if args.workspace:
        selected = runtime.workspace.select(args.workspace, source="cli_argument")
        console.print(f"[green]Çalışma alanı seçildi:[/green] {selected}")
    if args.select_workspace:
        selected = choose_workspace(runtime.workspace.root or Path.cwd())
        if selected is None:
            console.print("[yellow]Çalışma alanı seçimi iptal edildi.[/yellow]")
            return 1
        resolved = runtime.workspace.select(selected, source="folder_picker")
        console.print(f"[green]Çalışma alanı seçildi:[/green] {resolved}")
        return 0
    if args.workspace_info:
        console.print_json(json.dumps(runtime.status(), ensure_ascii=False))
        return 0
    return None


def main() -> int:
    args = parse_args()
    if args.visible:
        os.environ["OS_GEMINI_HEADED"] = "1"
    else:
        os.environ.setdefault("OS_GEMINI_HEADED", "0")
    os.environ.setdefault("OS_GEMINI_MODE", "cdp")
    os.environ.setdefault("OS_CLI_OWNS_SPINNER", "1")

    console = Console(highlight=False)
    registry: ProviderRegistry | None = None
    database: StateDatabase | None = None

    try:
        config = load_config(ROOT / "config.json")
        tool_runtime = LocalToolRuntime(config)
        workspace_result = configure_workspace(args, tool_runtime, console)
        if workspace_result is not None:
            return workspace_result

        database, sessions, memory = prepare_storage(config)
        registry = build_registry(config, tool_runtime)

        direct_result = run_direct_action(args, config, registry, database, console)
        if direct_result is not None:
            return direct_result

        app = TerminalApplication(config, registry, database, sessions, memory, console)
        return app.run()
    except KeyboardInterrupt:
        console.print("\n[dim]OS kapatıldı.[/dim]")
        return 130
    except OSErrorBase as exc:
        console.print(Panel(str(exc), title="[bold red]Kritik hata[/bold red]", border_style="red"))
        return 1
    except Exception as exc:
        console.print(
            Panel(
                f"{type(exc).__name__}: {exc}",
                title="[bold red]Beklenmeyen hata[/bold red]",
                border_style="red",
            )
        )
        return 1
    finally:
        if registry is not None:
            registry.close_all()
        if database is not None:
            database.checkpoint()


if __name__ == "__main__":
    raise SystemExit(main())
