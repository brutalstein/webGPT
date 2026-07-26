# Agent Skills ve güvenli plugin sistemi

## Paket biçimi

OS, Agent Skills `SKILL.md` düzenini kullanır:

```text
my-skill/
├── SKILL.md
├── scripts/       # isteğe bağlı; otomatik çalıştırılmaz
├── references/    # isteğe bağlı
├── assets/        # isteğe bağlı
└── .os-skill.json # OS kurulum provenance manifestosu
```

`SKILL.md` YAML frontmatter alanları:

```yaml
---
name: my-skill
description: Ne yaptığını ve hangi görevlerde kullanılacağını açıklar.
license: MIT
compatibility: OS Gemini workspace agent
allowed-tools:
  - read_file
metadata:
  domain: backend
---
```

## Progressive disclosure

1. **Catalog:** Gemini başlangıçta yalnızca `name`, `description`, `scope` ve aktif durumunu görür.
2. **Activation:** görev gerçekten eşleşirse `activate_skill` tam SKILL.md gövdesini yükler.
3. **Resources:** reference veya script metni ancak `read_skill_resource` çağrısıyla okunur.

Bu tasarım, çok sayıda skill kurulu olduğunda prompt büyümesini sınırlar.

`allowed-tools` yalnızca skill'in beklediği araçları bildiren metadata'dır. Yeni yetki vermez; OS araç allowlist'i, sandbox, onay ve risk politikaları her zaman üstündür.

## Skill kaynakları

Global skill alanı:

```text
%LOCALAPPDATA%\OS\skills
```

Güvenilen workspace içindeki proje skill alanları:

```text
.agents/skills
.os/skills
```

Process çalışma dizini otomatik workspace olduğunda proje skill'leri güvenilmiş sayılmaz. Klasörü CLI veya web seçiciyle açıkça seçmek güven sınırını oluşturur.

## GitHub'dan sohbet yoluyla kurulum

Kullanıcı public GitHub repository veya tree URL'sini verir:

```text
https://github.com/owner/repo/tree/main/skills/my-skill
```

Gemini iki aşamalı çalışır:

1. `inspect_github_skill`
2. `install_inspected_skill`

İlk aşama ağ erişimi olduğu için kullanıcı onayı ister ve yalnızca karantina alanına indirir. İkinci aşama dosya yazdığı için ayrıca kullanıcı onayı ister.

Bir repository birden fazla skill içeriyorsa `skill_path` verilmelidir. Slash içeren branch adlarında URL ayrıştırma belirsizliğini önlemek için `ref` ve `skill_path` ayrı argüman olarak kullanılabilir.

## Supply-chain kontrolleri

İnceleme katmanı:

- yalnızca `https://github.com` public kaynaklarını kabul eder;
- URL içindeki kullanıcı bilgisi ve özel portu reddeder;
- remote ref'i 40 karakterli commit SHA'ya çözümler;
- commit'i karantina klasörüne indirir;
- `.git` verisini kaldırır;
- symlink/path traversal ve iç içe `.git` alanlarını reddeder;
- dosya sayısı, tek dosya ve toplam boyut sınırlarını uygular;
- derlenmiş/yürütülebilir binary uzantılarını reddeder;
- bütün dosyaların SHA-256 özetini çıkarır;
- lisans frontmatter veya LICENSE dosyasını raporlar;
- script ve statik risk desenlerini raporlar;
- kaynağı commit SHA ile pinler;
- kurulumda `.os-skill.json` provenance manifestosu üretir;
- hedef skill'i atomik olarak değiştirir ve eski sürümü yedekler.

Lisans yoksa OS paketi açık kaynak olarak etiketlemez. Kaynak kodun GitHub'da görünmesi tek başına açık kaynak lisansı anlamına gelmez.

## Script politikası

Skill paketlerinde script bulunabilir fakat OS bunları otomatik çalıştırmaz. `read_skill_resource`, scripti yalnızca metin olarak modele gösterebilir. Komut çalıştırma yine mevcut allowlist, workspace sandbox ve kullanıcı onayı katmanına tabidir.

## Araçlar

- `list_skills`
- `activate_skill`
- `read_skill_resource`
- `inspect_github_skill`
- `install_inspected_skill`
- `uninstall_skill`

CLI katalog görünümü:

```powershell
.\os.bat --skills
```

Web arayüzündeki **Skills** sekmesi proje indeksini, kurulu skill'leri, lisans durumunu, kaynak sayısını ve risk bulgularını gösterir.
