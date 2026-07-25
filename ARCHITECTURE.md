# OS Mimarisi

```text
Terminal / Orchestrator
        |
        +-- Provider Registry
              |
              +-- GeminiChromeProvider
              |      |
              |      +-- ManualGeminiSetup
              |      |     Normal Chrome, otomasyonsuz hesap girişi
              |      |
              |      +-- GeminiBrowserController
              |      |     1) Doğrudan Chrome + IPv4 CDP
              |      |     2) Sandbox açık Playwright persistent fallback
              |      |
              |      +-- GeminiClient
              |      |     Prompt gönderimi, model seçimi, streaming yanıt
              |      |
              |      +-- GeminiDoctor
              |            Tanı, soft repair, yedekli reset
              |
              +-- ChatGPTManualWebProvider
```

## Güvenlik sınırı

Google hesabına giriş Playwright veya CDP açıkken yapılmaz. Oturum çerezleri normal Chrome aşamasında oluşturulur; otomasyon yalnızca daha sonra aynı standart dışı özel profile bağlanır.

## Gelecek katmanlar

- Provider yetenek bildirimi
- Araç izin sistemi ve komut allowlist'i
- Kalıcı semantic memory
- Proje çalışma alanı indeksleme
- Session özetleme ve context budgeting
- İnsan onaylı terminal tool execution
