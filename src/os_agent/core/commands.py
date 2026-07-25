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
  /use gemini                Gemini provider'ına ve son oturumuna geçer.
  /use chatgpt               ChatGPT kullanıcı kontrollü web köprüsüne geçer.
  /new                       Yeni yerel ve uzak konuşma başlatır.
  /sessions                  Son kalıcı oturumları gösterir.
  /resume OTURUM_ID          Kayıtlı oturuma ve uzak konuşma URL'sine döner.
  /session                   Aktif oturum ayrıntılarını gösterir.
  /remember anahtar=değer    Kalıcı yerel belleğe bilgi ekler.
  /forget anahtar            Kalıcı yerel bellekten bilgi siler.
  /memories                  Aktif provider bağlamını gösterir.
  /status                    Aktif provider durumunu gösterir.
  /help                      Bu yardımı gösterir.
  /exit                      OS'yi kapatır.
""".strip()
