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


## Yerel web kontrol düzlemi

```text
TerminalApplication ── Web seçeneği ──► FastAPI/Uvicorn (127.0.0.1)
                                            │
                               React/Vite production UI
                                            │ WebSocket events
                                            ▼
                                      AgentWorker(1)
                                            │
                         Orchestrator + Gemini + LocalToolRuntime
```

Web ve terminal iki ayrı ajan uygulaması oluşturmaz. Aynı provider registry, SQLite store, memory store ve tool runtime paylaşılır. Web kapandığında provider güvenli biçimde durdurulur ve terminal onay handler'ı geri yüklenir.

## Project intelligence plane

```text
User prompt
   │
   ├─► ProjectContextEngine
   │      ├─ git-aware file discovery
   │      ├─ incremental gzip cache
   │      ├─ line-addressable chunks
   │      └─ path boost + BM25 retrieval
   │
   ├─► SkillManager catalog (tier 1 metadata)
   │      ├─ global skills
   │      └─ trusted workspace skills
   │
   ▼
ToolProtocol initial contract
   │
   ├─ activate_skill (tier 2 instructions)
   ├─ read_skill_resource (tier 3 resource)
   └─ inspect/install GitHub skill (approval gated)
```

Project context and skills are services of `LocalToolRuntime`; they are not implemented inside the React UI or Gemini provider. CLI and web therefore share the same catalog, provenance, trust and retrieval behavior.

GitHub skill installation never imports downloaded Python modules into the OS process. Downloaded packages are instruction/resource data. Any future executable plugin host must remain a separate, disabled-by-default sandbox boundary.
