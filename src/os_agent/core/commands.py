from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Command:
    name: str
    argument: str = ""


def parse_command(text: str) -> Command | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:]
    name, _, argument = body.partition(" ")
    return Command(name=name.casefold(), argument=argument.strip())


HELP_TEXT = """
Komutlar:
  /providers                 Kullanılabilir provider'ları gösterir.
  /use gemini                Provider değiştirir.
  /use chatgpt               ChatGPT manuel web köprüsüne geçer.
  /new                       Yeni yerel OS oturumu oluşturur.
  /sessions                  Son yerel oturumları gösterir.
  /remember anahtar=değer    Kalıcı yerel belleğe bilgi ekler.
  /forget anahtar            Kalıcı yerel bellekten bilgi siler.
  /memories                  Aktif provider bağlamını gösterir.
  /status                    Aktif provider durumunu gösterir.
  /help                      Bu yardımı gösterir.
  /exit                      OS'yi kapatır.
""".strip()
