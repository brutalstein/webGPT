# OS

OS, Gemini ve ChatGPT konuşmalarını tek modern terminal arayüzünde yöneten kalıcı kişisel AI çalışma alanıdır.

## Başlatma

Windows'ta yalnızca:

```text
os.bat
```

çalıştırılır. Ana menüden Gemini veya ChatGPT seçebilir, yeni konuşma açabilir ya da kayıtlı bir konuşmaya devam edebilirsin.

## Provider çalışma biçimleri

### Gemini

Gemini ayrı Chrome profilinde arka planda otomatik çalışır:

```text
Terminal promptu → headless Chrome → Gemini yanıtı → terminal
```

### ChatGPT background companion

ChatGPT ayrı ve kalıcı Chrome profilinde çalışır. Chrome boşta minimize edilir; terminalden prompt girildiğinde prompt panoya kopyalanır ve yalnızca ChatGPT penceresi öne getirilir. Mesajı gönderme ve tamamlanan yanıtı panoya kopyalama adımı kullanıcı kontrollüdür. Yanıt alındıktan sonra pencere yeniden minimize edilir.

```text
Terminal promptu → pano → ChatGPT penceresi → kullanıcı gönderimi/kopyalaması
                 → SQLite kayıt → Chrome tekrar minimize
```

ChatGPT web çıktısı otomatik kazınmaz. OpenAI kullanım şartları veri veya Çıktının otomatik/programlı çıkarılmasını yasakladığı için bu provider uyumlu companion yaklaşımını kullanır.

## Hesap kurulumu

### Gemini

**Kurulum ve bakım → Google hesabı ve Gemini kurulumu** seçeneğini kullan. Kurulum normal Chrome'da ve otomasyon olmadan yapılır.

### ChatGPT

**Kurulum ve bakım → ChatGPT hesabı kurulumu** seçeneğini kullan. Ayrı profilde `ebru112263gundes@gmail.com` hesabına giriş yap; Özel Talimatlar, Bellek ve model ayarlarını doğrula.

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

ChatGPT için OS belleği varsayılan olarak prompta eklenir. Gemini için hesap içindeki kişisel talimatları değiştirmemek amacıyla bu özellik varsayılan olarak kapalıdır.

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

ChatGPT companion ayrıntıları `docs/CHATGPT_COMPANION.md` dosyasındadır.
