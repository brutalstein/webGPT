# OS Mimarisi

```text
os.bat
  └─ bootstrap.py
       └─ Modern TerminalApplication
            ├─ Conversation Picker
            ├─ Memory Manager
            ├─ Maintenance Menu
            ├─ Orchestrator
            │    ├─ GeminiWebProvider
            │    │    └─ Headless Chrome + CDP
            │    └─ OpenAIResponsesProvider
            │         ├─ Windows DPAPI Secret Store
            │         ├─ OpenAI HTTP Client
            │         ├─ Conversations API
            │         └─ Responses API
            └─ StateDatabase
                 ├─ sessions
                 ├─ messages
                 ├─ context_entries
                 ├─ events
                 └─ metadata
```

## Provider sınırları

### Gemini

- Hesap girişi normal Chrome'da otomasyon dışında yapılır.
- Normal kullanım headless Chrome + CDP'dir.
- Uzak Gemini konuşma URL'si yerel session ile eşlenir.

### ChatGPT / OpenAI

- ChatGPT web arayüzü otomatikleştirilmez ve kazınmaz.
- Resmi Responses ve Conversations API kullanılır.
- API anahtarı DPAPI ile şifrelenir veya `OPENAI_API_KEY` üzerinden alınır.
- Conversation ID yerel session'a bağlanır.
- Uzak state kaybolursa son 20 yerel turn yeni conversation'a replay edilebilir.

## Dayanıklılık

- SQLite WAL ve foreign key
- Atomik transaction'lar
- Quick-check
- Günlük yedek
- Provider state snapshot
- Request ID ve token usage metadata
- Retry/backoff: 408, 409, 429, 5xx
- API key hiçbir log, config, payload metadata veya veritabanına yazılmaz
