# OS

OS, Gemini ve ChatGPT konuşmalarını tek modern terminal arayüzünde yöneten kalıcı kişisel AI çalışma alanıdır.

## Başlatma

Windows'ta yalnızca:

```text
os.bat
```

çalıştırılır. Ana menüden Gemini veya ChatGPT seçebilir, yeni konuşma açabilir ya da kayıtlı bir konuşmaya devam edebilirsin.

## Hesap kurulumu

### Gemini

**Kurulum ve bakım → Google hesabı ve Gemini kurulumu** seçeneğini kullan. Kurulum normal Chrome'da ve otomasyon olmadan yapılır. Sonraki kullanımlarda Gemini arka planda çalışır.

### ChatGPT

**Kurulum ve bakım → ChatGPT hesabı kurulumu** seçeneğini kullan. Ayrı ve kalıcı Chrome profilinde `ebru112263gundes@gmail.com` hesabına giriş yap; Özel Talimatlar, Bellek ve model ayarlarını doğrula.

ChatGPT provider'ı web çıktısını otomatik olarak kazımaz. OS promptu panoya kopyalar ve doğru ChatGPT konuşmasını açar; mesajı gönderip tamamlanan yanıtı panoya kopyalama adımı kullanıcı kontrollüdür. Böylece hesap oturumu, konuşma URL'si, yerel mesaj kaydı ve OS belleği kalıcı tutulur.

## Modern CLI

Ana menüden:

- Son Gemini veya ChatGPT konuşmasına devam edebilirsin.
- Bütün provider konuşmalarını tek listede arayabilirsin.
- Yeni konuşma açarken provider seçebilirsin.
- Kalıcı OS belleğini global veya provider'a özel yönetebilirsin.
- Hesap kurulumu, tanı, onarım ve yedekleme işlemlerine ulaşabilirsin.

Sohbet ekranındaki komutlar:

```text
/menu   Ana menüye döner.
/new    Aynı provider ile yeni konuşma açar.
/exit   OS'yi kapatır.
```

## Kalıcı bellek ve kayıt

SQLite çalışma alanı:

```text
%LOCALAPPDATA%\OS\state\os-state.db
```

Kaydedilenler:

- Gemini ve ChatGPT kullanıcı/asistan mesajları
- Konuşma başlığı, provider ve zaman bilgileri
- Uzak konuşma URL'si
- Model ve çalışma modu
- Ayar ve context snapshot'ları
- Global ve provider'a özel kalıcı bellek
- Provider olayları ve hata kayıtları

ChatGPT için OS belleği varsayılan olarak prompta eklenir. Gemini için hesap içindeki kişisel talimatları değiştirmemek amacıyla bu özellik varsayılan olarak kapalıdır. Ayarlar `config.json` içindeki provider bazlı `inject_local_memory` alanıyla yönetilir.

Günlük yedekler:

```text
%LOCALAPPDATA%\OS\backups
```

## Görünür hata ayıklama

Gemini'yi görünür açmak için:

```powershell
.\os.bat --visible
```

Doğrudan bakım işlemleri:

```powershell
.\os.bat --setup
.\os.bat --doctor
.\os.bat --repair
.\os.bat --backup
```
