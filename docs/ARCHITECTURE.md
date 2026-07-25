# OS Mimarisi

```text
os.bat
  └─ bootstrap.py
       └─ Modern TerminalApplication
            ├─ Conversation Picker
            ├─ Maintenance Menu
            ├─ Orchestrator
            │    └─ GeminiWebProvider
            │         └─ Headless Chrome + CDP
            └─ StateDatabase
                 ├─ sessions
                 ├─ messages
                 ├─ context_entries
                 ├─ events
                 └─ metadata
```

## Tasarım ilkeleri

- Kök dizinde tek Windows giriş dosyası bulunur: `os.bat`.
- Gemini normal çalışmada headless Chrome ve IPv4 CDP ile arka planda çalışır.
- Google girişi otomasyon dışında, bakım menüsündeki normal Chrome kurulumu sırasında yapılır.
- Session ile Gemini uzak konuşma URL'si bire bir eşlenir.
- Konuşma seçildiğinde hem yerel mesaj kaydı hem uzak Gemini konuşması yeniden açılır.
- Kalıcı durum SQLite WAL, foreign key, transaction, quick-check ve düzenli backup ile korunur.
- Eski JSON kayıtları idempotent biçimde otomatik göç ettirilir.
- ChatGPT adapter kodu provider katmanında korunur fakat mevcut ürün yüzeyinde devre dışıdır.
