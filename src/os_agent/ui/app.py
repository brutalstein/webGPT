from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from ..config import AppConfig
from ..core.memory_store import MemoryStore
from ..core.orchestrator import Orchestrator
from ..core.provider_registry import ProviderRegistry
from ..core.session_store import SessionStore
from ..core.storage import StateDatabase
from ..errors import OSErrorBase, ProviderError
from ..tools import LocalToolRuntime


@dataclass(frozen=True, slots=True)
class MenuChoice:
    label: str
    value: str


class ArrowMenu:
    """Windows'ta ok tuşları, diğer ortamlarda numaralı seçim kullanan menü."""

    def __init__(self, console: Console):
        self.console = console

    def ask(self, message: str, choices: list[MenuChoice]) -> str | None:
        if not choices:
            return None
        if os.name == "nt" and sys.stdin.isatty():
            return self._ask_windows(message, choices)
        return self._ask_numbered(message, choices)

    def _ask_windows(self, message: str, choices: list[MenuChoice]) -> str | None:
        import msvcrt

        selected = 0
        with Live(
            self._render(message, choices, selected),
            console=self.console,
            refresh_per_second=20,
            transient=True,
        ) as live:
            while True:
                key = msvcrt.getwch()
                if key in {"\x00", "\xe0"}:
                    extended = msvcrt.getwch()
                    if extended in {"H", "K"}:  # yukarı / sol
                        selected = (selected - 1) % len(choices)
                    elif extended in {"P", "M"}:  # aşağı / sağ
                        selected = (selected + 1) % len(choices)
                    elif extended == "G":  # home
                        selected = 0
                    elif extended == "O":  # end
                        selected = len(choices) - 1
                    live.update(self._render(message, choices, selected))
                    continue
                if key in {"\r", "\n"}:
                    return choices[selected].value
                if key == "\x1b":
                    return None
                if key == "\x03":
                    raise KeyboardInterrupt
                if key.isdigit() and key != "0":
                    index = int(key) - 1
                    if index < len(choices):
                        return choices[index].value

    def _ask_numbered(self, message: str, choices: list[MenuChoice]) -> str | None:
        self.console.print(self._render(message, choices, selected=-1))
        index = IntPrompt.ask(
            "Seçim",
            choices=[str(item) for item in range(1, len(choices) + 1)],
            default=1,
            console=self.console,
        )
        return choices[index - 1].value

    @staticmethod
    def _render(message: str, choices: list[MenuChoice], selected: int) -> Panel:
        rows = Table.grid(padding=(0, 1))
        rows.add_column(width=3, justify="right")
        rows.add_column()
        for index, choice in enumerate(choices):
            active = index == selected
            marker = "›" if active else str(index + 1)
            style = "bold cyan" if active else "white"
            rows.add_row(Text(marker, style=style), Text(choice.label, style=style))
        footer = Text("↑↓ seç · Enter onayla · Esc geri", style="dim")
        return Panel(
            Group(rows, Text(""), footer),
            title=Text(message, style="bold"),
            border_style="cyan",
            padding=(1, 2),
        )


class TerminalApplication:
    """Gemini ve ChatGPT konuşmalarını ortak kalıcı çalışma alanında yönetir."""

    def __init__(
        self,
        config: AppConfig,
        registry: ProviderRegistry,
        database: StateDatabase,
        sessions: SessionStore,
        memory: MemoryStore,
        tool_runtime: LocalToolRuntime,
        root: Path,
        console: Console | None = None,
    ):
        self.config = config
        self.registry = registry
        self.database = database
        self.sessions = sessions
        self.memory = memory
        self.tool_runtime = tool_runtime
        self.root = root
        self.console = console or Console(highlight=False)
        self.menu = ArrowMenu(self.console)
        self.orchestrator = Orchestrator(
            config,
            registry,
            sessions,
            memory,
            config.default_provider,
        )
        self._exit_requested = False

    def run(self) -> int:
        self._render_header()
        while not self._exit_requested:
            action = self._main_menu()
            if action is None or action == "exit":
                break
            try:
                if action == "continue":
                    latest = self._latest_session()
                    if latest is None:
                        self._open_new_session()
                    else:
                        self._open_session(str(latest["session_id"]))
                elif action == "choose":
                    session_id = self._choose_session()
                    if session_id:
                        self._open_session(session_id)
                elif action == "new":
                    self._open_new_session()
                elif action == "web":
                    self._open_web_interface()
                elif action == "memory":
                    self._memory_menu()
                elif action == "maintenance":
                    self._maintenance_menu()
            except OSErrorBase as exc:
                self._show_error(str(exc))
            except KeyboardInterrupt:
                self.console.print("\n[dim]Ana menüye dönüldü.[/dim]")

        self.close()
        self.console.print("\n[dim]OS güvenli biçimde kapatıldı.[/dim]")
        return 0

    def close(self) -> None:
        try:
            self.orchestrator.flush()
        except Exception:
            pass
        self.registry.close_all()
        self.database.checkpoint()

    def _render_header(self) -> None:
        health = self.database.quick_check()
        subtitle = "Gemini · Web workspace · Kalıcı SQLite çalışma alanı"
        status = "[green]sağlıklı[/green]" if health.casefold() == "ok" else f"[red]{health}[/red]"
        self.console.print()
        self.console.print(
            Panel.fit(
                Text.from_markup(
                    "[bold cyan]OS[/bold cyan]\n"
                    f"[dim]{subtitle}[/dim]\n"
                    f"[dim]Depolama:[/dim] {status}"
                ),
                border_style="cyan",
                padding=(1, 4),
            )
        )

    def _latest_session(self) -> dict[str, Any] | None:
        rows = self.sessions.list_recent(limit=1, include_turns=False)
        return rows[0] if rows else None

    def _main_menu(self) -> str | None:
        latest = self._latest_session()
        choices: list[MenuChoice] = []
        if latest is not None:
            title = self._shorten(str(latest.get("title", "Yeni oturum")), 45)
            provider = self._provider_label(str(latest.get("provider", "")))
            choices.append(MenuChoice(f"Son konuşmaya devam et  ·  {provider} · {title}", "continue"))
        else:
            choices.append(MenuChoice("İlk konuşmayı başlat", "new"))
        choices.extend(
            [
                MenuChoice("Konuşma seç veya ara", "choose"),
                MenuChoice("Yeni konuşma", "new"),
                MenuChoice("Web çalışma alanını aç", "web"),
                MenuChoice("Kalıcı bellek ve bağlam", "memory"),
                MenuChoice("Kurulum ve bakım", "maintenance"),
                MenuChoice("Çıkış", "exit"),
            ]
        )
        return self.menu.ask("Ne yapmak istiyorsun?", choices)

    def _open_web_interface(self) -> None:
        from ..web import run_web_server

        self.orchestrator.suspend()
        self.console.print("[cyan]Yerel web çalışma alanı hazırlanıyor...[/cyan]")
        run_web_server(
            self.config,
            self.registry,
            self.database,
            self.sessions,
            self.memory,
            self.tool_runtime,
            self.root,
            self.console,
        )
        self.console.print("[dim]Web arayüzü kapandı; terminal menüsüne dönüldü.[/dim]")

    def _choose_provider(self, message: str = "Provider seç") -> str | None:
        names = self.registry.names()
        if not names:
            return None
        if len(names) == 1:
            return names[0]
        choices = []
        for name in names:
            if name == "gemini":
                label = "Gemini · arka plan Chrome"
            elif name == "chatgpt":
                label = "ChatGPT · OpenAI API · tam otomatik"
            else:
                label = self._provider_label(name)
            choices.append(MenuChoice(label, name))
        choices.append(MenuChoice("Geri", "__back__"))
        selected = self.menu.ask(message, choices)
        return None if selected in {None, "__back__"} else str(selected)

    def _choose_session(self, search: str | None = None) -> str | None:
        limit = max(5, int(self.config.cli.get("recent_session_limit", 30)))
        rows = self.sessions.list_recent(
            limit=limit,
            search=search,
            include_turns=False,
        )
        if not rows:
            self.console.print("[yellow]Eşleşen kayıtlı konuşma bulunamadı.[/yellow]")
            return None

        self._render_session_table(rows)
        choices = [
            MenuChoice(self._session_choice_label(item), str(item["session_id"]))
            for item in rows
        ]
        choices.extend(
            [
                MenuChoice("Konuşmalarda ara", "__search__"),
                MenuChoice("Ana menüye dön", "__back__"),
            ]
        )
        selected = self.menu.ask("Devam edilecek konuşmayı seç", choices)
        if selected in {None, "__back__"}:
            return None
        if selected == "__search__":
            query = Prompt.ask("[cyan]Başlık veya mesaj içinde ara[/cyan]", console=self.console).strip()
            return self._choose_session(query) if query else None
        return str(selected)

    def _render_session_table(self, rows: list[dict[str, Any]]) -> None:
        table = Table(title="Kayıtlı konuşmalar", border_style="cyan", header_style="bold cyan")
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("Provider", width=9)
        table.add_column("Başlık", min_width=24)
        table.add_column("Mesaj", justify="right", width=7)
        table.add_column("Model", width=14)
        table.add_column("Güncellendi", width=17)
        table.add_column("Uzak", justify="center", width=5)
        for index, item in enumerate(rows, start=1):
            state = item.get("provider_state", {})
            model = str(state.get("model", "—")) if isinstance(state, dict) else "—"
            remote = "✓" if isinstance(state, dict) and (state.get("remote_url") or state.get("conversation_id")) else "—"
            table.add_row(
                str(index),
                self._provider_label(str(item.get("provider", ""))),
                self._shorten(str(item.get("title", "Yeni oturum")), 44),
                str(item.get("message_count", 0)),
                self._shorten(model, 14),
                self._format_time(str(item.get("updated_at", ""))),
                remote,
            )
        self.console.print(table)

    def _open_session(self, session_id: str) -> None:
        record = self.sessions.get(session_id)
        if record is None:
            raise ProviderError(f"Oturum bulunamadı: {session_id}")
        provider = str(record.get("provider", ""))
        label = self._provider_label(provider)
        message = f"{label} konuşması hazırlanıyor..."
        with self.console.status(f"[cyan]{message}[/cyan]", spinner="dots"):
            self.orchestrator.resume_session(session_id)
        self._chat_loop()

    def _open_new_session(self) -> None:
        provider = self._choose_provider("Yeni konuşma için provider seç")
        if provider is None:
            return
        self.orchestrator.switch_provider(provider)
        label = self._provider_label(provider)
        with self.console.status(f"[cyan]Yeni {label} konuşması hazırlanıyor...[/cyan]", spinner="dots"):
            self.orchestrator.new_session()
        self._chat_loop()

    def _chat_loop(self) -> None:
        record = self.orchestrator.current_session()
        self._render_session_header(record)
        self._render_recent_history(str(record["session_id"]))

        while True:
            try:
                self.console.print("[dim]/menu · /new · /exit[/dim]")
                prompt = Prompt.ask("[bold cyan]Sen[/bold cyan]", console=self.console).strip()
            except (KeyboardInterrupt, EOFError):
                self.orchestrator.flush()
                return

            if not prompt:
                continue
            command = prompt.casefold()
            if command == "/menu":
                self.orchestrator.flush()
                return
            if command == "/exit":
                self.orchestrator.flush()
                self._exit_requested = True
                return
            if command == "/new":
                label = self._provider_label(self.orchestrator.provider_name)
                with self.console.status(f"[cyan]Yeni {label} konuşması hazırlanıyor...[/cyan]", spinner="dots"):
                    self.orchestrator.new_session()
                record = self.orchestrator.current_session()
                self._render_session_header(record)
                continue

            try:
                provider_name = self.orchestrator.provider_name
                label = self._provider_label(provider_name)
                with self.console.status(f"[cyan]{label} düşünüyor...[/cyan]", spinner="dots"):
                    response = self.orchestrator.send(prompt)
                self.console.print(
                    Panel(
                        Markdown(response.text),
                        title=f"[bold cyan]{self._provider_label(response.provider)}[/bold cyan]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
            except ProviderError as exc:
                self._show_error(str(exc))
            except KeyboardInterrupt:
                self.console.print("\n[yellow]İşlem kesildi; konuşma kaydı korundu.[/yellow]")

    def _render_session_header(self, record: dict[str, Any]) -> None:
        state = record.get("provider_state", {})
        model = str(state.get("model", "Hesap varsayılanı")) if isinstance(state, dict) else "—"
        remote = "bağlı" if isinstance(state, dict) and (state.get("remote_url") or state.get("conversation_id")) else "yeni"
        provider = self._provider_label(str(record.get("provider", "")))
        memory = "açık" if self._memory_injection_enabled(str(record.get("provider", ""))) else "kapalı"
        text = Text()
        text.append(str(record.get("title", "Yeni oturum")), style="bold")
        text.append(
            f"\n{record['session_id']}  ·  {provider}  ·  {model}  ·  uzak: {remote}  ·  OS belleği: {memory}",
            style="dim",
        )
        self.console.print(Panel(text, border_style="bright_black", padding=(0, 2)))

    def _render_recent_history(self, session_id: str) -> None:
        record = self.sessions.get(session_id)
        provider = self._provider_label(str(record.get("provider", ""))) if record else "Asistan"
        turns = self.sessions.recent_turns(session_id, limit=4)
        if not turns:
            return
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(width=10, style="dim")
        table.add_column()
        for turn in turns:
            role = "Sen" if turn["role"] == "user" else provider
            text = self._shorten(" ".join(str(turn["text"]).split()), 180)
            table.add_row(role, text)
        self.console.print(Panel(table, title="Son mesajlar", border_style="bright_black"))

    def _memory_menu(self) -> None:
        while True:
            choice = self.menu.ask(
                "Kalıcı bellek ve bağlam",
                [
                    MenuChoice("Kayıtlı belleği göster", "show"),
                    MenuChoice("Tüm provider'lar için bilgi ekle", "add_global"),
                    MenuChoice("Provider'a özel bilgi ekle", "add_provider"),
                    MenuChoice("Bellekten bilgi sil", "delete"),
                    MenuChoice("Ana menüye dön", "back"),
                ],
            )
            if choice in {None, "back"}:
                return
            if choice == "show":
                self._render_memory_entries()
            elif choice == "add_global":
                self._add_memory(None)
            elif choice == "add_provider":
                provider = self._choose_provider("Belleğin ait olacağı provider")
                if provider:
                    self._add_memory(provider)
            elif choice == "delete":
                self._delete_memory()

    def _add_memory(self, provider: str | None) -> None:
        scope = "genel" if provider is None else self._provider_label(provider)
        key = Prompt.ask(f"[cyan]{scope} bellek anahtarı[/cyan]", console=self.console).strip()
        if not key:
            return
        value = Prompt.ask("[cyan]Değer[/cyan]", console=self.console).strip()
        if not value:
            return
        self.memory.set(key, value, provider=provider)
        self.console.print(f"[green]Bellek kaydedildi:[/green] {scope} / {key}")

    def _delete_memory(self) -> None:
        entries = self.memory.list_entries()
        if not entries:
            self.console.print("[yellow]Silinecek bellek kaydı yok.[/yellow]")
            return
        choices = []
        for index, item in enumerate(entries):
            scope = "Genel" if item["scope"] == "global" else self._provider_label(item["provider"])
            label = f"{scope} · {item['key']} = {self._shorten(item['value'], 52)}"
            choices.append(MenuChoice(label, str(index)))
        choices.append(MenuChoice("Geri", "__back__"))
        selected = self.menu.ask("Silinecek kaydı seç", choices)
        if selected in {None, "__back__"}:
            return
        item = entries[int(selected)]
        provider = item["provider"] if item["scope"] == "provider" else None
        if self.memory.delete(item["key"], provider=provider):
            self.console.print("[green]Bellek kaydı silindi.[/green]")

    def _render_memory_entries(self) -> None:
        entries = self.memory.list_entries()
        if not entries:
            self.console.print("[yellow]Kalıcı bellek henüz boş.[/yellow]")
            return
        table = Table(title="Kalıcı OS belleği", border_style="cyan", header_style="bold cyan")
        table.add_column("Kapsam", width=12)
        table.add_column("Anahtar", width=24)
        table.add_column("Değer", min_width=32)
        table.add_column("Güncellendi", width=17)
        for item in entries:
            scope = "Genel" if item["scope"] == "global" else self._provider_label(item["provider"])
            table.add_row(
                scope,
                item["key"],
                self._shorten(item["value"], 90),
                self._format_time(item["updated_at"]),
            )
        self.console.print(table)

    def _maintenance_menu(self) -> None:
        while True:
            choice = self.menu.ask(
                "Kurulum ve bakım",
                [
                    MenuChoice("Google hesabı ve Gemini kurulumu", "setup_gemini"),
                    MenuChoice("OpenAI API bağlantısı", "setup_chatgpt"),
                    MenuChoice("Gemini sistem tanısı", "doctor"),
                    MenuChoice("Gemini oturumunu silmeden yumuşak onarım", "repair"),
                    MenuChoice("Şimdi veritabanı yedeği al", "backup"),
                    MenuChoice("Ayarlar ve depolama durumunu göster", "status"),
                    MenuChoice("Ana menüye dön", "back"),
                ],
            )
            if choice in {None, "back"}:
                return
            try:
                self.orchestrator.suspend()
                if choice == "setup_gemini":
                    self.registry.get("gemini").setup()
                elif choice == "setup_chatgpt":
                    self.registry.get("chatgpt").setup()
                elif choice == "doctor":
                    report = self.registry.get("gemini").doctor.run()
                    self.console.print(f"[green]Rapor oluşturuldu:[/green] {report}")
                elif choice == "repair":
                    self.registry.get("gemini").doctor.soft_repair()
                    self.console.print("[green]Yumuşak onarım tamamlandı.[/green]")
                elif choice == "backup":
                    path = self.database.backup_now(
                        self.config.backups_dir,
                        keep=max(1, int(self.config.storage.get("backup_keep", 10))),
                    )
                    self.console.print(f"[green]Yedek oluşturuldu:[/green] {path}")
                elif choice == "status":
                    self._render_status()
            except OSErrorBase as exc:
                self._show_error(str(exc))

    def _render_status(self) -> None:
        providers = Table(title="Provider durumları", border_style="cyan", header_style="bold cyan")
        providers.add_column("Provider")
        providers.add_column("Hesap")
        providers.add_column("Model")
        providers.add_column("Çalışma")
        providers.add_column("OS belleği")
        for name in self.registry.names():
            settings = self.config.provider(name)
            mode = "arka plan Chrome" if name == "gemini" else "Responses API · terminal"
            providers.add_row(
                self._provider_label(name),
                settings.expected_email,
                settings.preferred_model,
                mode,
                "açık" if self._memory_injection_enabled(name) else "kapalı",
            )
        self.console.print(providers)

        storage = Table(title="OS çalışma alanı", border_style="cyan", header_style="bold cyan")
        storage.add_column("Alan", style="dim")
        storage.add_column("Değer")
        storage.add_row("Veritabanı", str(self.config.database_path))
        storage.add_row("Veritabanı kontrolü", self.database.quick_check())
        storage.add_row("Yedek klasörü", str(self.config.backups_dir))
        storage.add_row("Bellek kaydı", str(len(self.memory.list_entries())))
        self.console.print(storage)

    def _memory_injection_enabled(self, provider: str) -> bool:
        settings = self.config.provider(provider)
        return bool(settings.get("inject_local_memory", self.config.inject_local_memory))

    def _session_choice_label(self, item: dict[str, Any]) -> str:
        title = self._shorten(str(item.get("title", "Yeni oturum")), 46)
        count = int(item.get("message_count", 0))
        updated = self._format_time(str(item.get("updated_at", "")))
        provider = self._provider_label(str(item.get("provider", "")))
        return f"{provider} · {title} · {count} mesaj · {updated}"

    def _show_error(self, message: str) -> None:
        self.console.print(
            Panel(
                Text(message),
                title="[bold red]Hata[/bold red]",
                border_style="red",
                padding=(1, 2),
            )
        )
        if "kurul" in message.casefold() or "oturum" in message.casefold():
            self.console.print("[dim]Ana menü → Kurulum ve bakım bölümünden ilgili bağlantıyı kurabilirsin.[/dim]")

    @staticmethod
    def _provider_label(provider: str) -> str:
        key = provider.casefold().strip()
        if key == "gemini":
            return "Gemini"
        if key == "chatgpt":
            return "ChatGPT"
        return provider or "Bilinmiyor"

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        clean = " ".join(text.split())
        return clean if len(clean) <= limit else clean[: max(1, limit - 1)].rstrip() + "…"

    @staticmethod
    def _format_time(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            return value[:16] or "—"
