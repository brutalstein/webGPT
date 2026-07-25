# OS — Provider tabanlı kişisel AI terminali

OS, terminal ile model sağlayıcıları arasında ortak bir provider katmanı kurar. Gemini provider'ı özel Google Chrome profilindeki oturumu kullanır; normal çalışmada Chrome penceresi göstermeden headless CDP modunda çalışır. ChatGPT provider'ı ise kullanıcı kontrollü web köprüsüdür ve ChatGPT çıktısını otomatik olarak kazımaz.

## İlk Gemini kurulumu

1. `doctor_gemini.bat` çalıştır.
2. `setup_gemini.bat` çalıştır.
3. Açılan normal Chrome'da `willieewonka224@gmail.com` hesabına giriş yap.
4. Gemini mesaj kutusunu, kişisel talimatlarını ve istediğin modeli doğrula.
5. Özel Chrome penceresini tamamen kapat.
6. `start_gemini.bat` çalıştır.

`start_gemini.bat` artık arka plan modudur. Görünür hata ayıklama gerektiğinde `start_gemini_visible.bat` kullanılabilir.

## Kalıcı oturumlar

Yerel session kayıtları şu dosyada tutulur:

```text
%LOCALAPPDATA%\OS\state\sessions.json
```

Her session aşağıdakileri saklar:

- Provider adı
- Kullanıcı/asistan mesajları
- Oturum başlığı
- Gemini veya ChatGPT uzak konuşma URL'si
- Kullanılan provider modu ve model bilgisi

Başlangıçta provider'ın en son session'ı otomatik yüklenir. Komutlar:

```text
/sessions
/session
/resume OTURUM_ID
/new
/use gemini
/use chatgpt
```

`/new`, hem yeni yerel session oluşturur hem de provider tarafında temiz konuşma açar. `/resume`, session hangi provider'a aitse ona geçer ve kayıtlı uzak konuşma URL'sini yeniden açar.

## Gemini çalışma modları

- `start_gemini.bat`: önerilen, arka plan CDP modu.
- `start_gemini_visible.bat`: görünür CDP modu.
- `start_gemini_background.bat`: arka plan CDP modu için uyumluluk kısayolu.
- `start_gemini_playwright_fallback.bat`: CDP başarısızsa persistent fallback.
- `setup_gemini.bat`: otomasyonsuz normal Chrome hesap kurulumu/oturum yenileme.

Projede `--no-sandbox` kullanılmaz. Gemini kişisel talimatlarına, Kişisel Zeka ayarlarına veya hesap ayarlarına kod dokunmaz. Promptun başına yerel bellek eklenmez (`inject_local_memory=false`).

## ChatGPT provider

ChatGPT provider ayrı kalıcı Chrome profilini ve uzak konuşma URL'sini session içinde korur. ChatGPT web çıktısının otomatik/programatik olarak çıkarılması uygulanmaz; provider görünür kullanıcı kontrollü pano köprüsü olarak kalır.

İlk kurulum:

```text
setup_chatgpt.bat
```

Beklenen hesap:

```text
ebru112263gundes@gmail.com
```

## Onarım araçları

- `doctor_gemini.bat`: Chrome, profil, loopback ve politika raporu.
- `repair_gemini_soft.bat`: çerezleri silmeden kilitleri ve önbellekleri temizler.
- `stop_gemini_browser.bat`: yalnızca OS Gemini Chrome süreçlerini kapatır.
- `reset_gemini_profile.bat`: profili yedekleyerek sıfırlar.
- `open_gemini_logs.bat`: tanı kayıtlarını açar.
