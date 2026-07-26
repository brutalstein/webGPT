# Proje bağlam motoru

## Amaç

Proje bağlam motoru, Gemini'nin her görevde bütün repository'yi tekrar tekrar taramasını önleyen yerel ve artımlı bir retrieval katmanıdır. İnternet servisi veya embedding API'si gerektirmez.

## Akış

```text
Workspace seçimi
      ↓
Git ls-files -co --exclude-standard
      ↓ (Git yoksa güvenli os.walk)
Metin/boyut/sandbox filtreleri
      ↓
mtime_ns + size tabanlı artımlı yeniden kullanım
      ↓
Satır aralıklı chunk'lar
      ↓
Path boost + BM25 retrieval
      ↓
Gemini başlangıç bağlamı + search_project_context
```

İndeks `%LOCALAPPDATA%\OS\state\project-context` altında gzip JSON olarak saklanır. Cache anahtarı workspace mutlak yolunun SHA-256 özetinden üretilir.

## Performans sınırları

Varsayılanlar:

- en fazla 3500 metin dosyası;
- dosya başına 256 KiB;
- toplam 6 MiB indekslenen metin;
- yaklaşık 1600 karakterlik chunk;
- 180 karakter overlap;
- otomatik prompt retrieval için en fazla 5 sonuç ve 9000 karakter.

Aynı boyut ve `mtime_ns` değerine sahip dosyaların chunk'ları yeniden kullanılabilir. Dosya yazma veya komut araçlarından sonra indeks kirli işaretlenir.

## Güvenlik

- Bütün yollar WorkspaceManager sandbox'ından geçer.
- Symlink ile workspace dışına çıkılamaz.
- `.agents/skills` ve `.os/skills` içerikleri bağlam indeksine alınmaz; skill talimatları yalnızca progressive disclosure ile yüklenir.
- Bağlam parçaları modele açıkça güvenilmeyen proje verisi olarak verilir.
- Proje içindeki bir metin sistem veya güvenlik sözleşmesini geçersiz kılamaz.

## Araçlar

- `project_context`: proje özeti ve indeks sağlığı.
- `search_project_context`: BM25 + path boost retrieval.
- `refresh_project_context`: artımlı veya zorlanmış yenileme.

CLI kontrolü:

```powershell
.\os.bat --refresh-context
.\os.bat --workspace-info
```

İndeks cache sayısı varsayılan olarak son 20 workspace ile sınırlandırılır; eski cache dosyaları otomatik temizlenir.
