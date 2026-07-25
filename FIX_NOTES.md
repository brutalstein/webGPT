# Bu Sürümde Düzeltilenler

- `--no-sandbox` tamamen kaldırıldı.
- Google hesabına giriş Playwright/CDP dışında normal Chrome'a taşındı.
- Chrome 136+ için standart dışı özel user-data-dir korunuyor.
- Birincil mod doğrudan Chrome subprocess + IPv4 CDP oldu.
- Boş port seçimi ve üç denemeli CDP açılışı eklendi.
- CDP başarısızsa sandbox açık persistent fallback eklendi.
- Otomasyon giriş engeli algılanınca tekrar deneme döngüsü yerine açıklayıcı hata veriliyor.
- Tek sekmeli çalışma korunuyor.
- Profil kilidi yalnızca OS profil süreçleri üzerinden temizleniyor.
- Çerezleri silmeyen soft repair, doctor ve yedekli reset eklendi.
- Kişisel talimatlar ve Gemini ayarlarına hiçbir otomatik müdahale yapılmıyor.
