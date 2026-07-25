# Gemini Yerel Araç Katmanı

OS, Gemini web provider'ını seçilmiş bir yerel çalışma alanına bağlayan modüler bir araç çalışma zamanı içerir.

## Çalışma alanı

İlk seçim:

```powershell
.\os.bat --select-workspace
```

Doğrudan yol:

```powershell
.\os.bat --workspace "C:\Projeler\Uygulama"
```

Durum:

```powershell
.\os.bat --workspace-info
```

Seçim `%LOCALAPPDATA%\OS\state\workspace.json` içinde kalıcı tutulur. Yol çözümleme, sembolik bağlantılar dahil olmak üzere seçilen kökün dışına çıkamaz.

## Yerleşik araçlar

Salt okunur:

- `workspace_info`
- `list_directory`
- `read_file`
- `search_text`
- `git_status`

Onay gerektiren:

- `write_file`
- `append_file`
- `replace_text`
- `create_directory`
- `run_command`

## Güvenlik

- Dosya yolları yalnızca seçili çalışma alanında çözülür.
- `.git` gibi korunan alanlara yazılmaz.
- Yazma öncesinde mevcut dosya tarihli bir yedeğe kopyalanır.
- Yazmalar geçici dosya ve atomik `os.replace` ile tamamlanır.
- Komutlar `shell=False` ile argüman listesi olarak çalıştırılır.
- Yalnızca allowlist içindeki programlar kullanılabilir.
- Yıkıcı Git ve sistem komutları regex politikasıyla engellenir.
- Yazma ve komut çağrıları terminalde açık kullanıcı onayı ister.
- Araç çağrıları `%LOCALAPPDATA%\OS\logs\tool-audit.jsonl` dosyasına denetim kaydı olarak yazılır.
- Dosya içeriği ve araç sonuçları güvenilmeyen veri kabul edilir; Gemini'ye bunları sistem talimatı olarak uygulamaması söylenir.

## Gemini araç döngüsü

1. OS, gerçek çalışma alanı durumu ve JSON şemalı araç manifestosunu Gemini'ye verir.
2. Gemini doğrulanan `os_tool_calls` zarfı üretir.
3. Yerel registry, politika, onay ve sandbox katmanları çağrıyı doğrular.
4. Araç yürütülür ve yapılandırılmış `os_tool_results` zarfı Gemini'ye döner.
5. Gemini gerekirse başka araç çağırır; tamamlandığında kullanıcıya normal yanıt verir.

Araç eklemek için `Tool` sınıfından türeyen yeni bir sınıf yazıp `ToolRegistry` içine kaydetmek yeterlidir. Provider, dosya sistemi ve politika katmanları birbirinden bağımsızdır.
