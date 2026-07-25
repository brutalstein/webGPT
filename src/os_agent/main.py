from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .config import AppConfig, load_config
from .core.commands import HELP_TEXT, parse_command
from .core.memory_store import MemoryStore
from .core.orchestrator import Orchestrator
from .core.provider_registry import ProviderRegistry
from .core.session_store import SessionStore
from .errors import OSErrorBase
from .providers.chatgpt_manual import ChatGPTManualWebProvider
from .providers.gemini_web import GeminiWebProvider

ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OS sağlayıcı tabanlı AI terminali")
    parser.add_argument("--provider", choices=("gemini", "chatgpt"), help="Başlangıç provider'ı")
    parser.add_argument("--setup", choices=("gemini", "chatgpt"), help="Provider hesap kurulumunu açar")
    parser.add_argument(
        "--reset-profiles",
        nargs="?",
        const="all",
        choices=("all", "gemini", "chatgpt"),
        help="Ayrılmış tarayıcı profillerini yedekleyip sıfırlar",
    )
    parser.add_argument("--doctor", action="store_true", help="Gemini Chrome tanı raporu üretir")
    parser.add_argument("--repair-gemini", action="store_true", help="Gemini profilini oturumu silmeden onarır")
    parser.add_argument("--stop-gemini", action="store_true", help="Yalnızca OS Gemini Chrome süreçlerini kapatır")
    return parser.parse_args()


def build_registry(config: AppConfig) -> ProviderRegistry:
    registry = ProviderRegistry(config)
    registry.register("gemini_chrome_cdp", lambda app, settings: GeminiWebProvider(app, settings))
    registry.register("chatgpt_manual_web", lambda app, settings: ChatGPTManualWebProvider(app, settings))
    return registry


def reset_profiles(config: AppConfig, registry: ProviderRegistry, target: str) -> None:
    targets = {"gemini", "chatgpt"} if target == "all" else {target}
    for name in targets:
        if name == "gemini":
            provider = registry.get("gemini")
            assert isinstance(provider, GeminiWebProvider)
            provider.doctor.backup_and_reset()
            continue
        path = config.data_dir / "browser-profiles" / "chatgpt"
        shutil.rmtree(path, ignore_errors=True)
        print(f"[PROFİL] Sıfırlandı: {path}")


def print_banner() -> None:
    print("=" * 74)
    print("             OS - PROVIDER TABANLI KİŞİSEL AI TERMİNALİ")
    print("=" * 74)


def print_status(orchestrator: Orchestrator) -> None:
    status = orchestrator.provider.status()
    for key, value in status.items():
        print(f"  {key}: {value}")
    print(f"  local_session: {orchestrator.session_id}")


def handle_command(command, orchestrator: Orchestrator, registry: ProviderRegistry) -> bool:
    if command.name in {"exit", "quit", "çıkış", "kapat"}:
        return False
    if command.name in {"help", "yardım"}:
        print(HELP_TEXT)
        return True
    if command.name == "providers":
        print("Provider'lar: " + ", ".join(registry.names()))
        return True
    if command.name == "use":
        if not command.argument:
            print("Kullanım: /use gemini veya /use chatgpt")
            return True
        orchestrator.switch_provider(command.argument)
        print(f"[PROVIDER] Aktif: {orchestrator.provider_name}")
        return True
    if command.name == "new":
        print(f"[OTURUM] Yeni oturum: {orchestrator.new_session()}")
        return True
    if command.name == "sessions":
        rows = orchestrator.sessions.list_recent(10)
        if not rows:
            print("Kayıtlı oturum yok.")
        for item in rows:
            print(
                f"- {item['session_id']} | {item['provider']} | "
                f"{len(item.get('turns', []))} mesaj | {item.get('updated_at', '')}"
            )
        return True
    if command.name == "remember":
        key, separator, value = command.argument.partition("=")
        if not separator:
            print("Kullanım: /remember anahtar=değer")
            return True
        orchestrator.memory.set(key, value)
        print(f"[BELLEK] Kaydedildi: {key.strip()}")
        return True
    if command.name == "forget":
        if not command.argument:
            print("Kullanım: /forget anahtar")
            return True
        removed = orchestrator.memory.delete(command.argument)
        print("[BELLEK] Silindi." if removed else "[BELLEK] Anahtar bulunamadı.")
        return True
    if command.name == "memories":
        entries = orchestrator.memory.combined(orchestrator.provider_name)
        if not entries:
            print("Yerel OS belleği boş.")
        for key, value in sorted(entries.items()):
            print(f"- {key}: {value}")
        return True
    if command.name == "status":
        print_status(orchestrator)
        return True

    print(f"Bilinmeyen komut: /{command.name}. /help yazabilirsin.")
    return True


def run_terminal(config: AppConfig, registry: ProviderRegistry, provider_name: str) -> int:
    sessions = SessionStore(config.sessions_path)
    memory = MemoryStore(config.memory_path)
    orchestrator = Orchestrator(config, registry, sessions, memory, provider_name)
    orchestrator.ensure_started()

    print(f"\n[HAZIR] Aktif provider: {provider_name}")
    print("Komutları görmek için /help yaz.\n")

    while True:
        try:
            text = input(f"OS[{orchestrator.provider_name}]> ").strip()
        except EOFError:
            break
        if not text:
            continue

        command = parse_command(text)
        if command is not None:
            if not handle_command(command, orchestrator, registry):
                break
            continue

        try:
            response = orchestrator.send(text)
            print(f"\n{response.provider}:\n{response.text}\n")
            print("-" * 74)
        except OSErrorBase as exc:
            print(f"\n[HATA] {exc}\n")
        except KeyboardInterrupt:
            print("\n[İPTAL] İşlem kesildi.\n")

    return 0


def main() -> int:
    args = parse_args()
    config = load_config(ROOT / "config.json")
    print_banner()
    registry = build_registry(config)

    try:
        if args.doctor:
            provider = registry.get("gemini")
            assert isinstance(provider, GeminiWebProvider)
            provider.doctor.run()
            return 0

        if args.repair_gemini:
            provider = registry.get("gemini")
            assert isinstance(provider, GeminiWebProvider)
            provider.doctor.soft_repair()
            return 0

        if args.stop_gemini:
            provider = registry.get("gemini")
            assert isinstance(provider, GeminiWebProvider)
            from .providers.gemini_chrome.processes import terminate_profile_processes

            stopped = terminate_profile_processes(provider.chrome_settings.profile_dir)
            print(f"[GEMINI] Kapatılan OS Chrome süreci: {len(stopped)}")
            return 0

        if args.reset_profiles:
            reset_profiles(config, registry, args.reset_profiles)
            return 0

        if args.setup:
            provider = registry.get(args.setup)
            provider.setup()
            return 0

        provider_name = args.provider or config.default_provider
        return run_terminal(config, registry, provider_name)
    except KeyboardInterrupt:
        print("\n[SİSTEM] Manuel olarak durduruldu.")
        return 130
    except OSErrorBase as exc:
        print(f"\n[KRİTİK HATA] {exc}")
        return 1
    except Exception as exc:
        print(f"\n[BEKLENMEYEN HATA] {type(exc).__name__}: {exc}")
        return 1
    finally:
        registry.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
