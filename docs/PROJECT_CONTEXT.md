# Sürekli proje zihin katmanı

## Amaç ve doğruluk sınırı

Proje zihin katmanı, Gemini'nin seçili workspace hakkında her görevde sıfırdan tahmin yürütmesini engeller. Dosya sistemi, kaynak kodu yapısı, Git durumu, yakın değişiklikler ve oturumda kullanılan dosyalar sürekli güncel bir yerel bilgi düzleminde tutulur.

Hiçbir bağlam sistemi değişmekte olan büyük bir repository için matematiksel olarak `%100` bilgi garantisi veremez. OS bunun yerine ölçülebilir bir güncellik sözleşmesi uygular:

- native dosya olaylarıyla arka plan invalidation;
- debounce sonrası artımlı yeniden indeksleme;
- periyodik tam doğrulama;
- her prompt öncesinde bounded freshness barrier;
- `dirty`, `generation`, watcher ve SQLite health metrikleri;
- kritik ayrıntılarda dosya araçlarıyla son doğrulama.

## Mimari

```text
Workspace
   │
   ├─ Watchdog native recursive observer
   │      └─ changed path journal + debounce
   │
   ├─ Git-aware discovery / safe walk fallback
   │
   ├─ Incremental text chunks
   │      └─ mtime_ns + size + analyzer version reuse
   │
   ├─ Tree-sitter structural analyzer
   │      ├─ classes / functions / methods / types
   │      ├─ imports
   │      └─ call/reference edges
   │
   ├─ SQLite WAL knowledge store
   │      ├─ files
   │      ├─ chunks + FTS5
   │      ├─ symbols + FTS5
   │      └─ dependency/reference edges
   │
   └─ Prompt context capsule
          ├─ project foundation
          ├─ query plan
          ├─ hybrid retrieval hits
          ├─ symbol hits
          ├─ graph neighbours
          ├─ recent changes
          └─ session working set
```

## Arka plan senkronizasyonu

`ProjectFileWatcher`, Watchdog'un işletim sistemine uygun native observer'ını recursive olarak başlatır. Değişiklikler doğrudan ağır indeks çalıştırmaz; path'ler birleştirilir ve varsayılan 450 ms debounce sonrasında tek refresh yapılır.

Watcher başlatılamazsa doğruluk tamamen kaybolmaz. Arka plan thread'i varsayılan 30 saniyede bir repository görünümünü doğrular. Bir kullanıcı prompt'u kirli indeks görürse en fazla `freshness_wait_ms` kadar arka plan refresh'ini bekler; hâlâ kirliyse prompt thread'i senkron refresh yapar.

Workspace değiştiğinde eski watcher, working set ve store kapatılır; yeni workspace için bütün katmanlar tekrar bağlanır.

## Yapısal analiz

Desteklenen kaynaklarda `tree-sitter-language-pack` parser'ları kullanılır. Parser nesneleri thread-local cache'te tutulur. Tree-sitter kullanılamayan bir dilde veya parse hatasında güvenli regex fallback devreye girer.

Çıkarılan yapı:

- sembol adı, qualified name, tür, satır aralığı ve imza;
- import/include/use ilişkileri;
- fonksiyon/metod çağrıları ve referanslar;
- parser backend ve parse-error metriği.

Skill klasörleri ve hassas dosyalar yapısal indekse de alınmaz.

## SQLite FTS5 bilgi deposu

Workspace başına ayrı SQLite dosyası oluşturulur:

```text
%LOCALAPPDATA%\OS\state\project-context\<workspace-hash>.context.sqlite3
```

Bağlantılar thread'ler arasında paylaşılmaz. Her işlem kendi bağlantısını açar; WAL, `busy_timeout`, foreign keys ve `synchronous=NORMAL` kullanılır. FTS5 bulunmayan Python derlemelerinde LIKE/fallback araması devam eder.

Gzip JSON cache geriye uyumlu proje özeti ve chunk reuse için korunur. SQLite store hızlı arama ve graph sorgularına hizmet eder.

## Retrieval ve bağlam kapsülü

Her prompt için niyet sınıflandırması yapılır: architecture, debug, test, change veya lookup. Bütçe buna göre ayarlanır.

Sıralama sinyalleri:

- SQLite FTS5 rank;
- path ve dosya adı eşleşmesi;
- tam ifade ve token coverage;
- önemli manifest/instruction dosyaları;
- oturum working set'i;
- yakın zamanda değişen dosyalar;
- MMR benzeri tekrar azaltma.

Gemini'ye bütün repository dökülmez. Proje omurgası ve görevle ilişkili parçalar kontrollü bütçeyle gönderilir. Ayrıntı gerektiğinde aşağıdaki araçlar kullanılır:

- `project_context`
- `search_project_context`
- `search_project_symbols`
- `project_impact`
- `context_health`
- `refresh_project_context`

## Oturum çalışma kümesi

Başarılı tool çağrılarında okunan veya değiştirilen relative path'ler session working set'e eklenir ve workspace SQLite deposunda oturum bazında kalıcı tutulur. Sonraki retrieval'da bu dosyalar kör biçimde zorlanmaz, yalnızca alakalı sonuçlar arasında kontrollü bir boost alır.

Yazma araçları hedef path'i dirty işaretler. `run_command`, test/build/codegen gibi geniş yan etkiler oluşturabileceği için tam doğrulama ister.

## Güvenlik

- Bütün yollar `WorkspaceManager` sandbox'ından geçer.
- Symlink ile workspace dışına çıkış engellenir.
- `.env`, credential ve anahtar kalıpları indekslenmez.
- `.agents/skills` ve `.os/skills` normal context'e alınmaz.
- Proje içeriği modele güvenilmeyen çalışma verisi olarak etiketlenir.
- SQLite ve gzip cache'e mümkün olan sistemlerde `0600` izin uygulanır.
- Context enrichment hatası gerçek tool işlemini başarısız hâle getirmez.

## Durum doğrulama

```powershell
.\os.bat --workspace-info
.\os.bat --refresh-context
```

Web arayüzündeki **Context & Skills** paneli dosya, sembol, ilişki, FTS5, generation, watcher ve dirty durumunu gösterir.
