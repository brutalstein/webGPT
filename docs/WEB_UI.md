# OS Yerel Agent Web Arayüzü

## Amaç

Web arayüzü, terminal çalışma alanındaki aynı Gemini provider, SQLite session store, kalıcı bellek ve yerel araç runtime'ını ikinci kez kopyalamadan kullanır. Arayüz yalnızca sunum ve etkileşim katmanıdır; dosya sistemi ve komut yetkileri backend politikasında kalır.

## Başlatma

```powershell
.\os.bat
```

Ana menüden **Web çalışma alanını aç** seçilir. Doğrudan başlatma:

```powershell
.\os.bat --web
```

Sunucu yalnızca `127.0.0.1` üzerinde dinler. Port doluysa yapılandırılmış porttan başlayarak sınırlı bir aralıkta boş port aranır.

## Mimari

```text
React 19 / Vite 8 production bundle
            │
            │ HTTP + WebSocket
            ▼
FastAPI local control plane
  ├─ tek kullanımlık açılış bileti
  ├─ HttpOnly yerel oturum cookie'si
  ├─ session/workspace/memory API'leri
  └─ sıralı ve sınırlandırılmış event hub
            │
            ▼
AgentWorker — tek iş parçacıklı kuyruk
  ├─ Playwright thread affinity
  ├─ Orchestrator
  ├─ GeminiWebProvider
  ├─ GeminiToolAgent
  └─ WebApprovalHandler
            │
            ├─ SQLite WAL session/memory
            └─ sandbox'lı LocalToolRuntime
```

## Olay akışı

WebSocket üzerinden taşınan başlıca olaylar:

- `chat.accepted`
- `generation.phase`
- `generation.snapshot`
- `generation.completed`
- `agent.started`, `agent.round`, `agent.completed`
- `tool.requested`, `tool.started`, `tool.completed`, `tool.failed`
- `approval.required`, `approval.resolved`, `approval.expired`
- `workspace.changed`
- `session.opened`

Event hub sabit boyutlu geçmiş ve kuyruk kullanır. Yavaş bir tarayıcı sınırsız bellek büyümesine yol açamaz; eski görsel olaylar kontrollü biçimde düşürülür. Playwright ve provider yaşam döngüsü tek worker thread'inde tutulur.

## Thinking ve streaming

OS gizli model düşüncesini istemez veya göstermez. Arayüz şu gözlemlenebilir aşamaları gösterir:

- prompt gönderildi, model yanıt başlangıcı bekleniyor: `Thinking`;
- araç çağrısı hazırlanıyor veya çalışıyor: `Tools`;
- Gemini sayfasında görünür cevap metni değişiyor: `Responding`.

Gemini DOM'unda kullanıcının zaten görebildiği yanıt metni değiştikçe `generation.snapshot` yayınlanır. Bu, token düzeyinde resmî API streaming'i değildir; görünür yanıtın güvenli ve artımlı bir snapshot akışıdır.

## Onay modeli

Yazma ve komut araçları worker thread'inde beklerken FastAPI event loop çalışmaya devam eder. Tarayıcı `approval.required` olayını modal olarak gösterir. Kullanıcı:

- bu çağrıyı onaylayabilir;
- aynı aracı aktif session boyunca onaylayabilir;
- reddedebilir.

Onay zaman aşımı varsayılan olarak 600 saniyedir. Web sunucusu kapanırken tüm bekleyen onaylar reddedilir.

## Dosya görünümü

Dosya paneli salt okunurdur ve seçilmiş workspace sandbox'ını kullanır. Aşağıdakiler uygulanır:

- çalışma alanı dışına çıkış engeli;
- symlink kaçış kontrolü;
- büyük dosya önizleme sınırı;
- binary ve UTF-8 olmayan dosyaları reddetme;
- `.git`, `node_modules`, `.venv`, build çıktıları gibi klasörleri görünümden çıkarma.

Dosya düzenlemeleri panel tarafından doğrudan yapılmaz; Gemini'nin doğrulanmış tool çağrısı ve mevcut güvenlik/onay katmanı üzerinden yürür.

## Frontend derleme performansı

OS iki ayrı hash tutar:

- `.deps-hash`: yalnızca paket tanımı değiştiğinde `npm install` çalıştırır;
- `.build-hash`: web kaynağı değiştiğinde `npm run build` çalıştırır.

Hazır üretim bundle'ı doğrudan FastAPI tarafından servis edilir. Normal kullanımda Vite geliştirme sunucusu, hot reload veya ikinci bir Node süreci çalışmaz.

## Güvenlik

- Sunucu yalnızca loopback adresine bağlanır.
- Tarayıcı ilk açılışta tek kullanımlık auth token kullanır.
- Sonraki istekler ayrı bir HttpOnly, SameSite=Strict cookie ile doğrulanır.
- WebSocket cookie ve `Origin` doğrulaması yapar.
- Değiştirici API istekleri özel yerel istek başlığı gerektirir.
- CSP, frame engeli, permission policy ve no-store API cache politikası uygulanır.
- Terminal komutları hâlâ `shell=False`, executable allowlist ve yıkıcı komut bloklarıyla çalışır.
