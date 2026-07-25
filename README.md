# OS

OS, Gemini ve OpenAI modellerini tek modern terminal arayüzünde yöneten kalıcı kişisel AI çalışma alanıdır.

## Başlatma

Windows'ta yalnızca:

```text
os.bat
```

çalıştırılır. Yeni konuşma açarken provider seçilir:

- **Gemini:** özel Chrome profili üzerinden arka planda çalışır.
- **ChatGPT:** resmi OpenAI Responses + Conversations API üzerinden çalışır; Chrome açılmaz ve pano adımı yoktur.

## İlk kurulum

### Gemini

**Kurulum ve bakım → Google hesabı ve Gemini kurulumu** seçeneğini kullan. Giriş normal Chrome'da ve otomasyon dışında yapılır. Sonraki kullanımlarda Gemini headless Chrome ile çalışır.

### ChatGPT / OpenAI

**Kurulum ve bakım → OpenAI API bağlantısı** seçeneğini kullan ve OpenAI API anahtarını gir. Anahtar:

- `config.json` içine yazılmaz.
- SQLite veritabanına yazılmaz.
- Windows DPAPI ile yalnızca mevcut Windows kullanıcısının açabileceği biçimde şifrelenir.
- İstenirse `OPENAI_API_KEY` ortam değişkeninden okunur.

Doğrudan kurulum komutu:

```powershell
.\os.bat --setup-openai
```

ChatGPT web aboneliği ve API platformu ayrı ürünlerdir. Plus/Pro aboneliği API kullanımını içermez; API hesabında ayrıca faturalandırma gerekir.

## Tam otomatik ChatGPT akışı

```text
Terminal promptu
      ↓
OS kalıcı bağlamı
      ↓
OpenAI Responses API
      ↓
Conversations API'deki kalıcı konuşma
      ↓
Yanıt doğrudan terminale
      ↓
SQLite mesaj + kullanım + istek kimliği kaydı
```

ChatGPT için Chrome, web scraper, Ctrl+V veya Ctrl+C kullanılmaz.

## Kalıcı konuşma ve hafıza

Yerel çalışma alanı:

```text
%LOCALAPPDATA%\OS\state\os-state.db
```

OpenAI tarafındaki konuşma kimliği de her OS session'ına bağlanır. Conversations API konuşmaları silinene kadar tutulur. Uzak konuşma kimliği kaybolursa OS, SQLite'taki son mesajları kullanarak yeni konuşmayı yeniden oluşturabilir.

Kaydedilenler:

- Gemini ve ChatGPT kullanıcı/asistan mesajları
- Provider ve model bilgisi
- Gemini uzak konuşma URL'si veya OpenAI conversation ID
- Son response ID ve API request ID
- Token kullanım metadatası
- Global ve provider'a özel kalıcı OS belleği
- Ayar/context snapshot'ları
- Provider olayları ve hata kayıtları

## Modern CLI

Ana menüden:

- Son konuşmaya devam et
- Konuşma seç veya ara
- Yeni konuşma
- Kalıcı bellek ve bağlam
- Kurulum ve bakım

Sohbet ekranındaki komutlar:

```text
/menu   Ana menüye döner.
/new    Aynı provider ile yeni konuşma açar.
/exit   OS'yi kapatır.
```

## Model ayarı

Varsayılan OpenAI modeli `config.json` içinde tanımlıdır:

```json
"preferred_model": "gpt-5.2"
```

Tercih edilen model erişilebilir değilse `model_fallbacks` sırasıyla denenir. Geçici model seçimi için:

```powershell
$env:OPENAI_MODEL="gpt-5-mini"
.\os.bat
```

## Bakım

```powershell
.\os.bat --setup
.\os.bat --setup-openai
.\os.bat --doctor
.\os.bat --repair
.\os.bat --backup
.\os.bat --visible
```

`--visible` yalnızca Gemini Chrome hata ayıklaması içindir. ChatGPT API provider'ı hiçbir durumda tarayıcı açmaz.
