# Değişiklik Günlüğü

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
