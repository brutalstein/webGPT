# Değişiklik Günlüğü

## 0.10.3

- Boş oluşturulan konuşma silme modalı gerçek, erişilebilir ve default-export edilen React bileşeniyle tamamlandı.
- React build öncesine boş kaynak dosyası, eksik yerel modül ve eksik default export denetimi eklendi.
- Sidebar ve konuşma silme regresyon testi sözdizimi düzeltildi; modalın focus trap ve kalıcı silme akışı doğrulandı.
- Gelecekte aynı sınıf bozuklukların Vite aşamasına kadar ilerlemeden açık dosya adıyla durdurulması sağlandı.

## 0.10.2

- Sol konuşma sidebar'ı masaüstü ve mobilde gerçekten kapatılıp yeniden açılabilir hâle getirildi.
- Konuşma listesine kalıcı silme düğmesi, erişilebilir doğrulama modalı ve eşzamanlı işlem koruması eklendi.
- Aktif konuşma silindiğinde worker/provider yaşam döngüsü güvenle ayrılıyor ve sıradaki konuşma otomatik açılıyor.
- SQLite session silme işlemi mesajlarda cascade temizliği, 404/409 API durumları ve regresyon testleriyle tamamlandı.

## 0.10.1

- Web arayüzündeki bütün etkileşimler bağlantı, yükleme ve eşzamanlı işlem durumlarına göre açıkça kilitlendi.
- Başarısız mesaj gönderiminde taslak geri yükleme, güvenli iptal durumu ve tekrar tıklama koruması eklendi.
- Session arama/yenileme yarışları, HTTP zaman aşımı ve stale intelligence cevabı sorunları giderildi.
- Global capability durumları Context & Skills paneline ve bootstrap API'sine bağlandı.
- Boş dosya kopyalama, pano hata geri bildirimi, modal stacking ve React error boundary eklendi.
- Activity detaylarının yapılandırılmış sonuçları gizlemesi ve alakasız temizleme düğmesi davranışı düzeltildi.

## 0.9.0

- Native recursive file watcher, debounce ve periyodik doğrulama içeren sürekli proje zihin katmanı eklendi.
- Prompt öncesi bounded freshness barrier ile kirli bağlamın senkron doğrulanması eklendi.
- Tree-sitter tabanlı sembol/import/call analizi ve regex fallback eklendi.
- Workspace başına SQLite WAL + FTS5 chunk/symbol deposu ve ilişki grafiği eklendi.
- Query planning, graph expansion, recent-change journal ve session working-set retrieval eklendi.
- `search_project_symbols`, `project_impact` ve `context_health` araçları eklendi.
- Tool sonuçları context working set ve invalidation sistemiyle bağlandı.
- Gemini nihai yanıtları için güvenli Markdown sözleşmesi, GFM tablolar/görev listeleri ve syntax highlighting eklendi.

## 0.8.0

- Artımlı proje bağlam motoru ve path-boosted BM25 retrieval eklendi.
- Git-aware dosya keşfi, gzip cache, değişmeyen chunk yeniden kullanımı ve otomatik context injection eklendi.
- Agent Skills uyumlu progressive-disclosure skill kataloğu eklendi.
- Public GitHub kaynakları için iki aşamalı karantina, provenance, lisans ve statik risk incelemesi eklendi.
- Global ve güvenilen proje skill alanları, skill aktivasyonu ve on-demand resource okuma eklendi.
- Skills/Context web inspector sekmesi ve CLI durum komutları eklendi.
- İndirilen scriptlerin otomatik çalıştırılması açıkça kapatıldı.

## 0.7.0

- Yerelde çalışan React/Vite coding-agent çalışma alanı eklendi.
- CLI ana menüsüne Web çalışma alanını aç seçeneği ve `--web` komutu eklendi.
- FastAPI/WebSocket kontrol düzlemi, tek thread Playwright worker ve bounded event hub eklendi.
- Gemini görünür yanıt snapshot'ları, thinking/responding/tools aşamaları ve iptal akışı eklendi.
- Tool çağrıları, süreleri, sonuçları ve kullanıcı onayları web arayüzünde görünür hâle getirildi.
- Workspace seçici, dosya ağacı, güvenli dosya önizlemesi, session listesi, bellek ve yedek yönetimi eklendi.
- Loopback-only sunucu, tek kullanımlık auth bileti, HttpOnly cookie, Origin doğrulaması ve CSP eklendi.
- Frontend bağımlılık ve kaynak hash'leriyle artımlı üretim eklendi.

## 0.5.0

- ChatGPT manuel Chrome/pano köprüsü kaldırıldı.
- Resmi OpenAI Responses + Conversations API provider'ı eklendi.
- ChatGPT artık yalnızca terminalden ve tamamen otomatik çalışır.
- API anahtarı Windows DPAPI kasasına taşındı.
- Conversation ID, response ID, request ID ve usage metadatası SQLite'a bağlandı.
- Retry/backoff ve hata sınıflandırması eklendi.
- Uzak conversation kaybında yerel geçmiş replay altyapısı eklendi.
- `--setup-openai` komutu eklendi.
