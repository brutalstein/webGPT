# Global executable capabilities

## Neden skill ve capability ayrılıyor?

`SKILL.md` bir agent talimatı ve kaynak paketidir. Python paketi, CLI, MCP server veya native program ise çalıştırılabilir bir **capability**'dir. OS bu iki güven alanını birbirine karıştırmaz:

```text
Agent Skill                         Global Capability
-----------                         -----------------
SKILL.md + references               executable package + isolated venv
prompt-time guidance                subprocess execution
no implicit code execution          explicit install consent
progressive disclosure              adapter allowlist + process limits
%LOCALAPPDATA%\OS\skills            %LOCALAPPDATA%\OS\extensions
```

Bir GitHub repository'sinde root `SKILL.md` bulunmaması artık otomatik reddetme nedeni değildir. `inspect_github_extension` repository'yi karantinada commit-pinned olarak inceler; desteklenen Python CLI manifestosu varsa executable extension olarak sınıflandırır.

## Global layout

```text
%LOCALAPPDATA%\OS\extensions\
├── packages\<name>\versions\<commit>\
│   ├── source\
│   ├── venv\
│   └── capability.json
├── data\<name>\<workspace-hash>\
├── quarantine\
└── backups\

%LOCALAPPDATA%\OS\state\capabilities.sqlite3
%LOCALAPPDATA%\OS\skills\<name>\SKILL.md
```

Capability bir kez kurulur. Workspace değiştiğinde aynı global executable kullanılır; proje içine venv, paket veya skill kopyalanmaz.

## Kurulum güven sınırı

1. Public GitHub URL yalnız `https://github.com` üzerinden kabul edilir.
2. Branch/tag hareketli haliyle kurulmaz; 40 karakterli commit SHA'ya çözülür.
3. Checkout öncesi symlink, submodule, binary, dosya sayısı ve boyut sınırları denetlenir.
4. `pyproject.toml`, package adı, console scripts, lisans ve statik risk sinyalleri raporlanır.
5. Kullanıcı ilk onayda yalnız incelemeye, ikinci onayda kuruluma izin verir.
6. İnceleme ve kurulum arasında bütün dosya SHA-256 hashleri tekrar karşılaştırılır.
7. Kurulum ayrı venv içinde, temizlenmiş environment ile subprocess olarak yapılır.
8. Güvenilen adapter yoksa `auto_start` ve `auto_query` açılamaz.

Bu model dependency ve process ağacını ayırır; Windows'ta Job Object, POSIX'te process group, timeout, memory ve bounded-output limitleri uygular. Kernel seviyesinde tam dosya sistemi/network sandbox değildir. Capability, OS kullanıcısının erişebildiği dosyalara teorik olarak erişebilir; bu yüzden yalnız güvenilen kaynaklar kurulmalıdır.

## Graphify adapter

Official eşleşme iki koşulu birlikte ister:

```text
repository = Graphify-Labs/graphify
package    = graphifyy
```

Yalnız package adının aynı olması otomatik güven sağlamaz.

Graphify çıktıları proje dışındadır:

```text
%LOCALAPPDATA%\OS\extensions\data\graphify\<workspace-hash>\graphify-out
```

OS kontrollü olarak `GRAPHIFY_OUT` ayarlar. Graph eksikse arka plan supervisor build başlatır; proje context generation yükseldiğinde coalesced update yapar. Mimari, çağrı akışı ve ilişki sorularında hazır graph otomatik `query_capability` preflight'ına eklenir. Sonuçlar kritik değişikliklerden önce kaynak dosya ve testlerle doğrulanır.

## Araçlar

- `list_capabilities`
- `capability_status`
- `inspect_github_extension`
- `install_inspected_extension`
- `query_capability`
- `run_capability`
- `configure_capability`
- `uninstall_capability`
