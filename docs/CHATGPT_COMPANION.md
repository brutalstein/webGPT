# ChatGPT Background Companion

## Amaç

ChatGPT provider'ı ayrı ve kalıcı bir Google Chrome profiliyle çalışır. Chrome normal kullanımda açık tutulur ancak boşta minimize edilir. Terminalden yazılan prompt Windows panosuna aktarılır; kullanıcı etkileşimi gerektiğinde yalnızca OS ChatGPT profiline ait Chrome penceresi öne getirilir. İşlem tamamlanınca pencere tekrar minimize edilir.

## Neden otomatik scraper değil?

OpenAI bireysel kullanım şartları, Hizmetlerden veri veya Çıktının otomatik/programlı olarak çıkarılmasını yasaklar. Bu nedenle provider:

- ChatGPT DOM'undan yanıt kazımaz.
- Koruyucu önlemleri veya kullanım sınırlarını aşmaya çalışmaz.
- Yanıtın seçilip panoya kopyalanmasını kullanıcı kontrolünde bırakır.
- Hesap oturumunu, konuşma URL'sini, yerel geçmişi ve OS belleğini kalıcı tutar.

Resmî kaynak: https://openai.com/tr-TR/policies/terms-of-use/

## Modüler yapı

```text
ChatGPTManualWebProvider
├── ManualChatGPTSetup
│   └── Otomasyonsuz normal Chrome hesabı kurulumu
├── PersistentBrowser
│   └── Ayrı kalıcı Chrome profili
├── ChatGPTWindowController
│   └── Yalnızca ilgili profil penceresini minimize/restore/focus
├── ClipboardExchange
│   └── Prompt aktarımı ve kullanıcı kontrollü yanıt doğrulama
└── SQLite SessionStore
    ├── Mesaj geçmişi
    ├── Uzak konuşma URL'si
    ├── Provider state
    ├── Ayar/context snapshot'ı
    └── Olay ve hata kayıtları
```

## Çalışma akışı

1. OS açıldığında ChatGPT konuşması seçilir.
2. Chrome ayrı profil ile headful başlar ve minimize edilir.
3. Terminal promptu panoya kopyalar.
4. ChatGPT penceresi öne getirilir.
5. Kullanıcı `Ctrl+V` ile mesajı gönderir.
6. Yanıt tamamlanınca kullanıcı yalnızca yanıtı seçip `Ctrl+C` yapar.
7. Terminal yanıtı SQLite'a kaydeder.
8. Chrome tekrar minimize edilir.

## Konfigürasyon

`config.json` içindeki ChatGPT alanları:

```json
{
  "interaction_mode": "background_companion",
  "background_idle": true,
  "restore_for_interaction": true,
  "minimize_after_exchange": true,
  "restore_clipboard_after_capture": false,
  "window_wait_seconds": 15,
  "clipboard_retry_count": 3,
  "output_capture": "user_controlled_clipboard"
}
```

`restore_clipboard_after_capture=true` yapılırsa yanıt SQLite'a alındıktan sonra önceki pano içeriği geri yüklenir.

## Güvenlik sınırları

- Pencere yönetimi yalnızca `%LOCALAPPDATA%\OS\browser-profiles\chatgpt` profilini kullanan Chrome süreçleriyle sınırlandırılır.
- Normal günlük Chrome profiline dokunulmaz.
- `--no-sandbox` kullanılmaz.
- Parola veya oturum çerezi uygulama tarafından okunmaz.
- Windows dışındaki sistemlerde native pencere yönetimi güvenli no-op olarak davranır.
