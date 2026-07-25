from __future__ import annotations

from pathlib import Path


def choose_workspace(initial: Path | None = None) -> Path | None:
    """Windows/macOS/Linux üzerinde klasör seçici açar; GUI yoksa terminal yolunu sorar."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="OS çalışma alanını seç",
            initialdir=str(initial or Path.cwd()),
            mustexist=True,
        )
        root.destroy()
        return Path(selected) if selected else None
    except Exception:
        value = input(f"Çalışma alanı klasörü [{initial or Path.cwd()}]: ").strip()
        return Path(value) if value else (initial or Path.cwd())
