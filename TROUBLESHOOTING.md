# Gemini Chrome Sorun Giderme Matrisi

Web arayüzleri, hesap güvenliği, Chrome güncellemeleri ve kurum politikaları değişebildiği için hiçbir paket gelecekteki bütün hataları kesin olarak öngöremez. Bu belge güncel resmi Chrome, Google ve Playwright davranışlarına göre en olası hata sınıflarını önceden ele alır.

## 1. “Desteklenmeyen komut satırı işareti: --no-sandbox”

**Neden:** Playwright/Chromium sandbox kapalı başlatılmıştır. Güvenlik seviyesi düşer ve Chrome uyarı verir.

**Bu sürümde çözüm:**

- Birincil CDP modunda Chrome doğrudan başlatılır ve `--no-sandbox` eklenmez.
- Persistent fallback modunda `chromium_sandbox=True` kullanılır.
- Eski ZIP'i değil bu paketi yeni klasörden çalıştır.

## 2. “Oturumunuz açılamadı / Bu tarayıcı veya uygulama güvenli olmayabilir”

**Nedenler:**

- Google giriş sayfası otomasyon yazılımı altında açılmıştır.
- Güvensiz/uyumsuz eklenti vardır.
- JavaScript kapalıdır.
- Antivirüs web kalkanı veya TLS denetimi giriş akışına müdahale ediyordur.
- Kurumsal Chrome politikası uygulanıyordur.

**Çözüm yolları:**

1. Otomasyon penceresinde giriş yapma.
2. `setup_gemini.bat` çalıştır; normal Chrome'da giriş yap.
3. Pencereyi tamamen kapat; sonra `start_gemini_visible.bat` çalıştır.
4. `doctor_gemini.bat` ile politika/proxy/antivirüs raporuna bak.
5. Normal Chrome'da JavaScript ve çerezlerin açık olduğunu kontrol et.
6. Web koruması/TLS incelemesi yapan güvenlik yazılımını geçici test amacıyla devre dışı bırakmak yerine önce ürün ayarlarından Google/Chrome istisnası araştır.

## 3. `TargetClosedError`

**Nedenler:**

- Chrome penceresi veya Gemini sekmesi kullanıcı tarafından kapatılmıştır.
- Profil başka bir Chrome süreci tarafından kilitlenmiştir.
- Chrome çökmüştür veya kurum politikası tarafından kapatılmıştır.
- Eski bir context/page nesnesi yeniden kullanılmıştır.

**Çözüm:**

- `stop_gemini_browser.bat` çalıştır.
- `repair_gemini_soft.bat` çalıştır.
- Tekrar `start_gemini_visible.bat` çalıştır.
- Gerekirse `reset_gemini_profile.bat` ile yedekli sıfırlama yap.

## 4. Chrome sürekli sekme açıp kapatıyor

**Neden:** Eski sürüm hesap kontrolü için periyodik yeni sekme oluşturuyordu.

**Bu sürümde çözüm:** Hesap doğrulama sekmesi, hesap seçici döngüsü ve yeni sekme polling'i yoktur. Başlangıçta tek Gemini sekmesi seçilir ve diğer OS profil sekmeleri kapatılır.

## 5. CDP portu açılamıyor

**Olası nedenler:**

- Chrome 136+ ile varsayılan Chrome `User Data` dizini kullanılmaktadır.
- Profil kilitlidir.
- Seçilen loopback portu başka işlem tarafından alınmıştır.
- Güvenlik duvarı/antivirüs `127.0.0.1` trafiğine müdahale etmektedir.
- Chrome erken kapanmıştır.
- Kurumsal politika uzaktan hata ayıklamayı engelliyordur.

**Bu sürümde önlemler:**

- Standart dışı özel `--user-data-dir` kullanılır.
- Her denemede boş IPv4 portu seçilir.
- En fazla üç farklı port denenir.
- Yalnızca OS profiline ait eski süreçler kapatılır.
- Başlangıç logu kaydedilir.
- CDP başarısızsa sandbox açık persistent fallback denenebilir.

**El ile çözüm:**

1. `doctor_gemini.bat`
2. `stop_gemini_browser.bat`
3. `repair_gemini_soft.bat`
4. `start_gemini_playwright_fallback.bat`

## 6. Chrome açılıp hemen kapanıyor

**Olası nedenler:** profil bozulması, disk/izin sorunu, Chrome politikası, güvenlik yazılımı, eski lock dosyaları veya hatalı Chrome güncellemesi.

**Çözüm:**

- Doctor raporunu çalıştır.
- Soft repair uygula.
- Chrome'u normal biçimde açıp güncelle.
- Son çare olarak yedekli profil sıfırlaması yap.

## 7. Hesap oturumu süresi doldu

**Belirti:** Gemini yerine `accounts.google.com` açılır veya giriş düğmesi görünür.

**Çözüm:** Otomasyon altında giriş yapma. `setup_gemini.bat` ile normal Chrome'da oturumu yenile.

## 8. Kişisel talimatlar uygulanmıyor

**Kontrol listesi:**

- Normal kurulum Chrome'unda doğru Google hesabı açık mı?
- Talimatlar aynı hesapta etkin mi?
- Yeni sohbet açıldı mı?
- Prompt başka bir davranışla açıkça çelişiyor mu?
- Hesap/plan özelliği o anda erişilebilir mi?
- Gemini arayüzü farklı bir çalışma alanı/hesaba mı geçti?

**Projede yapılanlar:** Talimat ekranına dokunulmaz, prompt değiştirilmez ve yeni sohbet açılmaya çalışılır. En güvenilir test: `Bana nasıl hitap etmen gerekiyor?`

## 9. 3.1 Pro seçilemiyor

**Nedenler:** model adı arayüzde değişmiş olabilir, hesap planı/kota/dağıtım durumu farklı olabilir veya model seçici DOM'u değişmiş olabilir.

**Çözüm:** `strict_model_check=false` olduğu için mesaj engellenmez. Görünür Chrome'da modeli elle seç; terminal `/status` ile algılanan metni kontrol et.

## 10. Gemini mesaj kutusu bulunamıyor

**Nedenler:** giriş/izin ara ekranı, bölgesel erişim, çerez bildirimi, sayfa yükleme hatası veya Gemini DOM değişikliği.

**Çözüm:**

- Görünür modda ara ekranı tamamla.
- Yenile ve yeni sohbet aç.
- Log ekran görüntüsünü incele.
- `repair_gemini_soft.bat` çalıştır.
- Arayüz değiştiyse `selectors.py` güncellenmelidir.

## 11. Yanıt başlamıyor veya bitmiyor

**Nedenler:** ağ kopması, Gemini kota/yoğunluk mesajı, gönder düğmesi değişikliği, streaming DOM değişikliği veya çok uzun üretim.

**Önlemler:**

- 60 saniye yanıt başlama beklemesi.
- Yanıt metni sabitlenmesi ve “durdur” düğmesinin kaybolması birlikte kontrol edilir.
- Toplam süre dolarsa alınabilen son metin döndürülür.

## 12. Antivirüs, VPN, proxy veya TLS incelemesi

**Belirtiler:** normal Chrome'da da giriş reddi, sertifika hatası, CDP endpoint'e erişememe veya sayfanın sürekli yeniden yüklenmesi.

**Çözüm:** Doctor raporundaki antivirüs/proxy bilgisini incele; kurum/ürün dokümantasyonundan Chrome ve Google alanları için güvenli istisna yapılandır. Kurumsal cihazlarda BT ekibine danış.

## 13. Kurumsal Chrome politikaları

Playwright'ın resmi dokümantasyonu, kurum politikalarının Chrome'u başlatma ve kontrol etme kabiliyetini etkileyebileceğini belirtir. Doctor, HKCU/HKLM Chrome politika anahtarlarını raporlar. Yönetilen cihazda politika kaldırmaya çalışma; BT ile görüş.

## 14. OneDrive ve uzun yol sorunları

Projeyi OneDrive içinde çalıştırmak sanal ortam ve senkronizasyon kilitleri üretebilir. Profil zaten `%LOCALAPPDATA%` altında tutulur; proje klasörü için `C:\OS` gibi kısa ve yerel bir yol tercih et.

## 15. Python/Playwright kurulumu

`start.bat` `.venv` oluşturur ve yalnızca gereksinim karması değiştiğinde paketleri kurar. Python 3.10+ ve PATH/`py` launcher gerekir. Kurulum hatalarında proxy/sertifika ayarları etkili olabilir.

## 16. Son çare sırası

1. `doctor_gemini.bat`
2. `stop_gemini_browser.bat`
3. `setup_gemini.bat`
4. `start_gemini_visible.bat`
5. `repair_gemini_soft.bat`
6. `start_gemini_playwright_fallback.bat`
7. `reset_gemini_profile.bat`
8. Yeniden `setup_gemini.bat`
