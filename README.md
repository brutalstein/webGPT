# OS — Chrome/Gemini Provider

Bu sürüm Gemini hesabını **normal Google Chrome profilinde** tutar ve terminalden gönderilen promptları Gemini web arayüzüne aktarır.

## Neden iki aşamalı?

Google, yazılım otomasyonu altında çalışan giriş sayfalarını engelleyebilir. Bu nedenle:

1. Google hesabına giriş **normal Chrome ile, Playwright/CDP olmadan** yapılır.
2. Giriş tamamlandıktan sonra aynı özel profil Chrome+CDP ile kontrol edilir.

Bu tasarımda `--no-sandbox` kullanılmaz. Gemini kişisel talimatlarına, Kişisel Zeka ayarlarına veya hesap ayarlarına kod dokunmaz.

## İlk kurulum

1. ZIP'i normal bir klasöre çıkar. OneDrive dışındaki `C:\OS` gibi kısa bir yol tercih edilir.
2. `doctor_gemini.bat` çalıştır.
3. `setup_gemini.bat` çalıştır.
4. Açılan normal Chrome'da `willieewonka224@gmail.com` hesabına giriş yap.
5. Gemini mesaj kutusunu, kişisel talimatlarını ve istediğin modeli kontrol et.
6. Bu özel Chrome penceresini tamamen kapat, terminale dönüp Enter'a bas.
7. `start_gemini_visible.bat` çalıştır.

Terminal açıldığında test promptu:

```text
Bana nasıl hitap etmen gerekiyor?
```

## Başlatma seçenekleri

- `start_gemini.bat`: önerilen görünür CDP modu.
- `start_gemini_visible.bat`: görünür CDP modu.
- `start_gemini_background.bat`: oturum çalıştıktan sonra isteğe bağlı headless mod.
- `start_gemini_playwright_fallback.bat`: CDP çalışmazsa sandbox açık Playwright persistent fallback.
- `setup_gemini.bat`: otomasyonsuz normal Chrome hesap kurulumu/oturum yenileme.

## Onarım araçları

- `doctor_gemini.bat`: Chrome sürümü, profil, kilitler, loopback, politika, proxy ve antivirüs raporu.
- `repair_gemini_soft.bat`: çerezleri silmeden kilitleri ve önbellekleri temizler.
- `stop_gemini_browser.bat`: yalnızca OS Gemini profiline ait Chrome süreçlerini kapatır.
- `reset_gemini_profile.bat`: profili silmeden tarihli klasöre yedekler ve boş profil oluşturur.
- `open_gemini_logs.bat`: tanı loglarını açar.

## Kritik kural

Otomasyon penceresinde Google giriş ekranı görünürse orada giriş yapmaya çalışma. Pencereyi kapatıp `setup_gemini.bat` çalıştır. Google'ın otomasyon altındaki giriş sayfasını engellemesi normal bir güvenlik davranışıdır.

## Profil konumu

Önceki çalışan profil varsa uyumluluk için şu dizin kullanılır:

```text
%LOCALAPPDATA%\GeminiTerminalAgent\chrome-profile
```

Yoksa:

```text
%LOCALAPPDATA%\OS\browser-profiles\gemini-chrome
```

## Talimatların korunması

Gemini kişisel talimatları hesabın içindedir. Proje:

- Ayarlar menüsünü otomatik açmaz.
- Talimat anahtarlarını değiştirmez.
- Promptun başına yerel bellek eklemez (`inject_local_memory=false`).
- Yeni sohbet açarak hesap ayarlarının temiz bir sohbette uygulanmasını sağlar.

Daha ayrıntılı hata matrisi için `TROUBLESHOOTING.md` dosyasına bak.
