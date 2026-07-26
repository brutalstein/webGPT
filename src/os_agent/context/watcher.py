from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Iterable


ChangeCallback = Callable[[set[str], str], None]


class ProjectFileWatcher:
    """Native OS eventlerini güvenli relative path kümelerine dönüştürür.

    watchdog platformun native backend'ini seçer. Observer başlatılamazsa watcher
    devre dışı kalır; ProjectContextEngine periyodik doğrulama taramasıyla doğruluğu
    korur.
    """

    def __init__(
        self,
        root: Path,
        callback: ChangeCallback,
        *,
        ignored_directories: Iterable[str] = (),
        excluded_prefixes: Iterable[str] = (),
    ):
        self.root = root.resolve()
        self.callback = callback
        self.ignored = {str(item).casefold().strip("/\\") for item in ignored_directories if str(item).strip()}
        self.excluded = {
            str(item).replace("\\", "/").casefold().strip("/")
            for item in excluded_prefixes
            if str(item).strip()
        }
        self._observer = None
        self._lock = threading.Lock()
        self.backend = "disabled"
        self.error: str | None = None

    def _relative(self, value: str) -> str | None:
        try:
            target = Path(value).resolve(strict=False)
            relative = target.relative_to(self.root).as_posix().strip("/")
        except (OSError, ValueError):
            return None
        if not relative:
            return None
        folded = relative.casefold()
        parts = [item.casefold() for item in Path(relative).parts]
        if any(part in self.ignored for part in parts[:-1]):
            return None
        if any(folded == prefix or folded.startswith(prefix + "/") for prefix in self.excluded):
            return None
        return relative

    def start(self) -> bool:
        with self._lock:
            if self._observer is not None:
                return True
            try:
                from watchdog.events import FileSystemEventHandler
                from watchdog.observers import Observer
            except Exception as exc:
                self.error = f"watchdog kullanılamıyor: {exc}"
                return False

            owner = self

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event) -> None:  # type: ignore[override]
                    paths: set[str] = set()
                    source = owner._relative(str(getattr(event, "src_path", "")))
                    destination = owner._relative(str(getattr(event, "dest_path", "")))
                    if source:
                        paths.add(source)
                    if destination:
                        paths.add(destination)
                    if not paths:
                        return
                    event_type = str(getattr(event, "event_type", "changed"))
                    if bool(getattr(event, "is_directory", False)):
                        event_type = "directory-" + event_type
                    owner.callback(paths, event_type)

            try:
                observer = Observer(timeout=0.5)
                observer.schedule(Handler(), str(self.root), recursive=True)
                observer.start()
            except Exception as exc:
                try:
                    observer.stop()
                except Exception:
                    pass
                self.error = f"dosya watcher başlatılamadı: {exc}"
                return False
            self._observer = observer
            self.backend = type(observer).__name__
            self.error = None
            return True

    def stop(self) -> None:
        with self._lock:
            observer = self._observer
            self._observer = None
        if observer is None:
            return
        try:
            observer.stop()
            observer.join(timeout=5)
        except Exception:
            pass
        self.backend = "stopped"
