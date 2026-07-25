# Değişiklik Günlüğü

## 0.4.0 — Modern CLI ve kurumsal kayıt katmanı

- Kök dizindeki çok sayıdaki `.bat` dosyası kaldırıldı; tek giriş `os.bat` oldu.
- Renkli ve ok tuşlarıyla kullanılan interaktif ana menü eklendi.
- Konuşma seçme, arama, son konuşmaya devam etme ve yeni konuşma akışları menüye taşındı.
- Sohbet içi komutlar `/menu`, `/new` ve `/exit` ile sınırlandı.
- JSON session/memory yapısı SQLite tabanlı ortak çalışma alanına yükseltildi.
- WAL, transaction, foreign key, quick-check, olay günlüğü ve otomatik yedekleme eklendi.
- Eski JSON kayıtları için otomatik ve tekrar çalıştırılabilir göç eklendi.
- Ayar ve context snapshot'ları session kaydına bağlandı.
- Gemini varsayılan olarak arka planda çalışmaya devam ediyor.
