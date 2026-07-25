# OS

OS, terminalden Gemini ile konuşmak için tek giriş noktalı kişisel AI çalışma alanıdır. Google Chrome özel profilde ve normal kullanımda tamamen arka planda çalışır.

## Başlatma

Windows'ta yalnızca şu dosyayı çalıştır:

```text
os.bat
```

İlk açılışta **Kurulum ve bakım → Google hesabı ve Gemini kurulumu** seçeneğine gir. Açılan normal Chrome'da Google hesabına giriş yap, Gemini'nin çalıştığını doğrula ve Chrome'u tamamen kapat. Sonraki kullanımlarda `os.bat` Chrome penceresi göstermeden çalışır.

## Modern CLI

Ana menüden:

- Son konuşmaya devam edebilirsin.
- Kayıtlı konuşmaları seçebilir veya içeriklerinde arama yapabilirsin.
- Yeni bir Gemini konuşması başlatabilirsin.
- Kurulum, tanı, onarım ve yedekleme işlemlerine ulaşabilirsin.

Sohbet ekranında yalnızca üç komut vardır:

```text
/menu   Ana menüye döner.
/new    Yeni konuşma açar.
/exit   OS'yi kapatır.
```

## Kalıcı kayıt

OS çalışma alanı SQLite üzerinde tutulur:

```text
%LOCALAPPDATA%\OS\state\os-state.db
```

Her konuşmada şu bilgiler transaction ile kaydedilir:

- Kullanıcı ve Gemini mesajları
- Konuşma başlığı ve zaman bilgileri
- Gemini uzak konuşma URL'si
- Model ve tarayıcı çalışma modu
- O konuşmada geçerli olan ayarların anlık görüntüsü
- Yerel context'in anlık görüntüsü
- Provider olayları ve hata kayıtları

Eski `sessions.json` ve `memory.json` dosyaları ilk çalıştırmada otomatik olarak SQLite'a taşınır. Günlük otomatik yedekler burada tutulur:

```text
%LOCALAPPDATA%\OS\backups
```

## Görünür hata ayıklama

Normal kullanımda gerekmez. Chrome'u görünür açarak test etmek için:

```powershell
.\os.bat --visible
```

Doğrudan bakım işlemleri de desteklenir:

```powershell
.\os.bat --setup
.\os.bat --doctor
.\os.bat --repair
.\os.bat --backup
```

Mimari ve teknik ayrıntılar `docs/` klasöründedir.
