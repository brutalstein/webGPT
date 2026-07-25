# Sorun Giderme

## Gemini oturumu bulunamadı

`os.bat` aç, **Kurulum ve bakım → Google hesabı ve Gemini kurulumu** seç. Normal Chrome'da giriş yap, Gemini mesaj kutusunu doğrula ve Chrome'u tamamen kapat.

## Arka planda mesaj kutusu bulunamadı

Önce kurulum menüsünü tamamla. Tanı için:

```powershell
.\os.bat --visible
```

## CDP veya profil kilidi

Ana menüden **Kurulum ve bakım → Oturumu silmeden yumuşak onarım** seç. Bu işlem çerezleri ve Google oturumunu silmez.

## Veritabanı

Durum dosyası:

```text
%LOCALAPPDATA%\OS\state\os-state.db
```

Yedekler:

```text
%LOCALAPPDATA%\OS\backups
```

OS açılışta `PRAGMA quick_check` çalıştırır. Ayrıca bakım menüsünden anlık yedek alınabilir.

## Eski sohbetler görünmüyor

İlk açılışta `%LOCALAPPDATA%\OS\state\sessions.json` otomatik göç edilir. Eski dosyanın kopyası `backups\legacy-json` altında korunur.
