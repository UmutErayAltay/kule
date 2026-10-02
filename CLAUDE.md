# CLAUDE.md

Bu dosya, Claude Code bu repoda çalışırken bağlam olarak otomatik okunur.
Detay için `README.md`'ye bakın; bu dosya yalnızca "hızlıca doğru şeyi
yapmak" için gereken özet bilgidir.

## Proje nedir

kule: Umut'un projelerinin durumunu tek panelde toplayan kontrol
kulesi. Git repo durumları, cor (claude-openrouter) proxy sağlığı,
BorsaSite pipeline'ı, readbunny DB'si, Mt3Ui55OS vault hijyeni,
atlas/orkestra durum sayıları, bağımlılık tarama özeti ve otomatik
bakım uyarılarını tek bir FastAPI panelinde birleştirir. kule kendi
başına veri üretmez — dokuz ayrı dış kaynağa (dosya sistemi/HTTP/
Postgres/süreç listesi/alt süreç) bağlanıp okur, biri çökerse yalnızca
o kartı "erişilemiyor" gösterir.

## Komutlar

```bash
pip install -e .                # bağımlılıklar + paket, editable
cp config.yaml.example config.yaml   # sonra gerçek değerlerini gir
uvicorn app.main:app            # sunucuyu başlat (http://127.0.0.1:8000)
```

Not: `pyproject.toml::[project.scripts]` altında `kule = "app.cli:main"`
var. Sunucuyu başlatmanın yolu `kule` ya da doğrudan
`uvicorn app.main:app`; `kule --notify-once` ise sunucuyu hiç
başlatmaz (bkz. altta "Bilinen sınırlamalar").

Testler `tests/` altında, `python -m pytest` ile çalışır (dev bağımlılığı:
`pip install -e ".[dev]"`). Yeni kod eklerken test yazacaksan aşağıdaki
"Test yazarken" kısmındaki kısıtlara uy.

## Mimari — bir bakışta

```
app/
  config.py           # config.yaml yükleyici — yoksa ConfigError, example'a SESSİZCE düşmez
  aggregator.py        # 10 collector'ı paralel çağırır, her biri izole, 60sn TTL cache
  notifier.py         # özet -> kısa Türkçe uyarı metni + Telegram Bot API'ye gönderim
  cli.py              # `kule` komutu: sunucuyu başlatır veya --notify-once ile tek seferlik bildirim
  collectors/
    git_status.py      # repo_roots altında .git tarar, branch/dirty/son commit
    cor_status.py       # cor'un /healthz, /dashboard/api/health, /dashboard/api/metrics-summary uçları
    borsasite_status.py # HTTP health + opsiyonel Postgres (trade_decisions/predictions)
    readbunny_status.py # Postgres links tablosundan özet (reachable/last_updated/error/pending/total)
    vault_status.py     # Mt3Ui55OS vault'unu okur: kırık wikilink, GERÇEK yetim (ne alan ne veren; kök .md ve daily/ hariç), açık Threads.md hikaye sayısı
    maintenance_status.py # Dalga F: disk doluluğu + unutulmuş süreçler + eski kirli repo (SADECE raporlar)
    durum_status.py     # `durum --json` sözleşmesinin ORTAK koşucusu (subprocess + çıktı doğrulama)
    atlas_status.py     # `atlas durum --json` -> pushlanmamış/README/bulgu/todo sayıları (kirli repo/repo sayısı OKUNMAZ: canlı `git` kartıyla örtüşürdü)
    orkestra_status.py  # `orkestra durum --json` -> görev/onay/kota sayıları
    harita_status.py    # `harita durum --json` — modül duruyor ama aggregator'a KAYITLI DEĞİL (panelde kartı yok, vault kartı yeterli)
    bagimlilik_status.py # `~/.bagimlilik/son.json` RAPOR DOSYASI (alt süreç DEĞİL) -> açık repo / kritik+yüksek / denetlenemedi
  main.py              # FastAPI app — GET / (panel HTML), GET /api/summary (aggregator JSON)
  web/
    page.py             # render_dashboard_html() — statik HTML iskelet (veri içermez)
    script.py            # DASHBOARD_JS — sayfa yüklenince /api/summary'yi çeker, DOM'u doldurur
    styles.py             # DASHBOARD_CSS — cor'un görsel dilinden (koyu tema, keskin köşe) esinli, farklı accent
config.yaml.example    # şablon, secret alanları boş
config.yaml            # gerçek değerler — .gitignore'da, ASLA commit edilmez
```

Akış: `config.yaml` (`app/config.py::load_config`) → on collector
(`app/collectors/*.py::collect(config)`) → `app/aggregator.py::
collect_all` (paralel + izolasyon) / `get_cached_summary` (60sn TTL) →
`app/main.py` (`GET /api/summary` bu JSON'u döner, `GET /` panel
HTML'ini döner) → `app/web/script.py` tarayıcıda `/api/summary`'yi
çekip DOM'u dolduruyor.

Bildirim yolu bunun ayrı bir dalı: `app/cli.py --notify-once` →
`get_cached_summary` → `app/notifier.py::build_alert_message` (sorun
varsa metin, yoksa `None`) → `send_telegram_message`. Panelden
tamamen bağımsız çalışır.

`app/main.py`, `app/web/page.py`'ı **lazy import** eder ve bulamazsa
(ImportError) açıklayıcı bir 500 döner — iki dosya paralel ajanlar
tarafından farklı dalgalarda yazıldığı için birbirine sert bağımlı
olmamaları bilinçli bir tasarım (bkz. `app/main.py` docstring'i).
Aynı sebeple `config.yaml` eksikse de sunucu **exit etmez**, her
istekte 500 + "ne yapman gerektiğini" söyleyen bir mesaj döner.

## Yeni bir collector eklerken

1. `app/collectors/<kaynak>_status.py` oluştur. Kardeş dosyalardaki
   deseni birebir takip et:
   - Tek bir `collect(config: dict) -> dict | list[dict]` fonksiyonu
     dışa açılır, başka bir şey import edilmez.
   - `collect` **asla raise etmez** — dış çağrı (HTTP/DB/subprocess/
     dosya okuma) başarısız olursa `{"error": "..."}` (veya
     `borsasite_status`/`readbunny_status` gibi `{"reachable": False,
     "error": "..."}`) döner. Bu, `aggregator.py::_run_isolated`'ın
     zaten yaptığı izolasyonun ÜSTÜNE ikinci bir güvenlik ağıdır —
     collector kendi içindeki try/except'i eksik bırakırsa bile
     aggregator paniklemez, ama collector kendi hata şeklini
     kendisi belirlemeli (panel o şekle göre render eder).
   - Config'de ilgili anahtar eksik/boşsa (örn. `database_url: ""`)
     hiç bağlanmayı DENEME — doğrudan açıklayıcı bir error dön
     (bkz. `cor_status.py`, `readbunny_status.py`).
   - Postgres/psycopg gibi ağır veya opsiyonel bağımlılıkları
     fonksiyon içinde lazy import et (bkz. `borsasite_status.py`,
     `readbunny_status.py`) — modül üst seviyesinde değil.
2. `app/aggregator.py::collect_all` içindeki `jobs` dict'ine bir satır
   ekle: `"<kaynak>": (yeni_modül.collect, config)`. Otomatik keşif
   YOK, kayıt elle yapılır.
3. `app/web/page.py`'a bir `stat-tile` + kart ekle, `app/web/script.py`'a
   karşılık gelen `render<Kaynak>` fonksiyonunu ve DOM id'lerini ekle.
   **DOM id'leri `page.py` ile `script.py` arasında birebir eşleşmek
   zorunda** — biri değişip diğeri değişmezse JS `qs()` null döner,
   sayfa sessizce boş kalır, hiçbir hata fırlamaz (bkz. `page.py`
   docstring'i).
4. `README.md`'deki `/api/summary` JSON şeklini güncelle.
5. `app/notifier.py::build_alert_message` yeni kaynak şeklini tanıyor mu
   kontrol et — ÜÇ dönüş şekli vardır (dict+`reachable`, dict+yalnızca
   `error`, dict listesi) ve `_describe` bunları ayrı ayrı ele alır.
   `maintenance` bu üçünün de dışında bir DÖRDÜNCÜ şekildir (alt
   bölümlerden oluşan dict) ve `build_alert_message` içinde
   `MAINTENANCE_KEY` eşleşmesiyle ayrı bir yola (`_maintenance_sentences`)
   yönlendirilir; kaynak kotasına (`MAX_LISTED_SOURCES`) girmez.
   `atlas`/`orkestra` (ve kayıtlı olursa `harita`) ise BEŞİNCİ yoldur: erişilemezlikleri
   `_describe`'ın mevcut dalına uyar (`{"reachable": False, "error":
   <sabit kod>}`), ama `reachable: True` iken sayı alanlarındaki uyarıları
   `_durum_sentences` üretir ve `DURUM_SOURCES` eşleşmesiyle yönlendirilir.
   Yeni collector farklı bir şekil döndürüyorsa ya `_describe`'a dal
   ekle ya da `build_alert_message`'de kendi yolunu yaz, yoksa o kaynağın
   hataları bildirimde hiç görünmez.
   **`DURUM_SOURCES` = `YAPILANDIRILMAMIS_KODLAR` anahtarlarıdır**: yeni bir
   `dict` + `reachable` kaynağı eklediğinde `notifier.py`'deki eşlemeye de
   bir satır düşmezsen `config_yok` muafiyetini almaz ve her cron çalışmasında
   "erişilemiyor (config_yok)" mesajı üretir. Muafiyet **koda değil kaynağa**
   bağlıdır: `bagimlilik` raporu dosyadan okuduğu için `okunamadi` de
   muaftır (araç hiç çalışmamış demektir), atlas'taki `okunamadi` ise
   gerçek arızadır ve uyarır.

## Kritik kurallar (bunları asla bozma)

- **`config.yaml` commit edilmez** — `.gitignore`'da zaten var, secret
  gerektiren alanlar (Telegram token, DB URL) örnek dosyada boş
  bırakılır. `config.yaml.example`'a asla gerçek bir secret yazma.
- **Secret'lar env'den akar, dosyadan değil**: Telegram token/chat_id
  `KULE_TELEGRAM_BOT_TOKEN` / `KULE_TELEGRAM_CHAT_ID` env değişkenleri
  ile yaml'daki değerin önüne geçer (bkz. `app/config.py::load_config`).
  Yeni bir secret alanı eklersen aynı deseni kullan.
- **Hardcode path yok**: her yol `config.yaml` üzerinden gelir
  (`repo_roots`, `vault.path`, ...), `Path`/`pathlib` ile işlenir.
- **Bir kaynağın çökmesi paneli düşürmez**: `aggregator.py` her
  collector'ı ayrı thread'de, ayrı try/except'te çalıştırır. Yeni bir
  collector eklerken bu izolasyonu bozacak bir kısayol (örn. birden
  fazla kaynağı tek fonksiyonda birleştirip ortak bir try/except'e
  sokmak) YAPMA.
- **Koşulsuz `reachable`/başarı YASAK**: alttaki HTTP/DB çağrısının
  gerçekten başardığını doğrula (`raise_for_status()`, gerçek satır
  döndü mü, vs.) — `cor_status.py`/`borsasite_status.py`/
  `readbunny_status.py` deseni budur, taklit et.
- **DOM id senkronizasyonu**: `app/web/page.py` ve `app/web/script.py`
  aynı sözleşmeyi konuşur (yukarıdaki madde 3). Biri değişirse diğeri
  de değişmeli.
- **Bildirim de asla raise etmez, paneli de kule'yi de düşürmez**:
  `notifier.py::send_telegram_message` yalnızca True/False döner.
  Token/chat_id boşsa ağa hiç çıkmaz. Panelden veya CLI'dan bir hata
  yüzünden bildirim kaybı, kule'yi durdurmamalı.
- **Bakım botu YALNIZCA raporlar**: `maintenance_status.py` hiçbir
  süreci öldürmez, dosya silmez, git komutu çalıştırmaz. Bu bir tespit
  katmanıdır, müdahale katmanı değil — `psutil.Process.kill`/`terminate`
  ya da `shutil.rmtree` gibi bir çağrı eklenirse bu madde ihlal edilmiş
  demektir (`test_collect_does_not_kill_anything` bunu kazara değil,
  bilerek güvenceye alır).
- **Koleksiyon içi config tek noktadan default'lanır**: eşikler
  (`disk_threshold_percent`, `stale_process_hours`, `stale_git_days`) ve
  `process_names` `maintenance_status`'ın `_positive_number`/`_name_patterns`
  yardımcılarından geçer. `maintenance:` bloğu **tamamen opsiyoneldir** —
  eksik, bozuk (`"maintenance": 5`), sıfır/negatif ya da yanlış tipli
  (`True`, liste) olsa bile collector default'larla çalışır. Yeni bir
  eşik eklerken aynı yardımcıdan geçir, yoksa bozuk config collector'ı
  sessizce düşürür.
- **Alt süreçten hiçbir ham metin dışarı çıkmaz** (`atlas`/`orkestra`/
  `harita`): alt sürecin stdout/stderr'i, komut yolu, ortam değişkeni ve
  istisna metni ASLA panele ya da Telegram'a girmez. Panele yalnızca
  (a) doğrulanmış sayılar/kısa etiketler ve (b) `durum_status.py`'deki
  **SABİT** hata kodları (`config_yok`, `komut_yok`, `zaman_asimi`,
  `cikti_gecersiz`, `calistirilamadi`, `bilinmeyen_hata`) çıkar. Kaynağın
  kendi `hata` kodu sabit listede (`db_yok`/`indeks_yok`/`okunamadi`)
  değilse **içeriği kopyalanmaz** — `bilinmeyen_hata` yazılır. Biri
  `str(e)` ya da `proc.stderr`'ı mesaja koyarsa bu madde ihlal edilmiş
  demektir (`test_no_secret_leak*` testleri bunu kazara değil, bilerek
  güvenceye alır).
- **Dosyadan okunan kaynakta da ham metin dışarı çıkmaz** (`bagimlilik`):
  raporun JSON metni, repo yolları, paket adları ve `str(e)` ASLA panele
  ya da Telegram'a girmez — kule yalnızca doğrulanmış SAYILARI okur,
  gerisini (`aciklar`, `ayrinti`, `desteklenmeyen`, `yol`, `ad`) bilerek yok
  sayar. Tek yol `config["bagimlilik"]["dosya"]`'dandır (hardcode yol yok).
- **`bool` bir sayı DEĞİLDİR**: `int`'in alt türü olduğu için
  `isinstance(True, int)` doğrudur; `{"repo_sayisi": true}` geçerli JSON'dur
  ama "1 repo" demek değildir. Doğrulama `is_count()`'ten geçer.
- **Bilinmeyen sayı `null`'dur, `0` değil**: sözleşme bunu açıkça ister.
  Panelde `null` → "bilinmiyor" (`script.py::count`), JS'te `|| 0` gibi bir
  kısayol sessizce "ölçtüm ve sıfır" uydurur — kullanma.
- **`KULE_TELEGRAM_*` alt sürece GEÇİRİLMEZ** (`durum_status.py::
  _child_env`): kule'nin Telegram secret'ı alt sürecin `env`'inde
  görünmemeli.

## Test yazarken

- **Dış servise gerçek ağ isteği YASAK** (cor/BorsaSite HTTP'sine,
  gerçek Telegram API'sine, readbunny/borsasite Postgres'ine, gerçek
  internete) — collector testleri `httpx`/
  `psycopg` çağrılarını mock'lamalı veya `config` içindeki URL'leri
  boş bırakıp "config eksik" dalını test etmeli. `git_status.py` gibi
  saf dosya-sistemi tabanlı collector'lar için `tmp_path` ile gerçek
  bir sahte repo ağacı kurmak tercih edilir (mock'lamadan).
- Repo kökünde gerçek bir `config.yaml` varsa (geliştirici makinesinde)
  testler onu OKUMAMALI — `load_config(path=...)` her zaman açık bir
  yol veya izole bir tmp dosyasıyla çağrılmalı.

## Bilinen sınırlamalar (bilerek eklenmedi, şaşırma)

- **Telegram bildirimi yalnızca isteğe bağlı tetiklenir**: `app/
  notifier.py` gönderimi yapar, ama panelin içinden otomatik olarak
  çalışmaz — `kule --notify-once` (cron/systemd timer) veya elle çağrı
  gerekir. Yani "sunucu ayakta olduğu sürece otomatik uyaran" bir
  sürüm değil; sürekli izleme istenirse bir scheduler eklenmeli.
  Bildirim metni de `sendMessage`'e gider, `parse_mode` yok — Telegram'ın
  Markdown'ı yorumlanmaz.
- **`--notify-once` sadece uyarır, çözümlemez**: eşik/gecikme/eski
  kalma süresi gibi kavramlar yok; her çalıştırmada sorun varsa mesaj
  gönderir (spam koruması = cron'un frekansı).
- **Tek process, tek worker varsayımı**: `aggregator.py`'daki cache
  process-içi bellekte (`Lock` + dict), çoklu worker/process senaryosu
  düşünülmedi — birden fazla `uvicorn` worker'ı ile çalıştırılırsa her
  worker kendi cache'ını tutar.

Git geçmişi dalga dalga ilerledi: `343dcee` veri toplama katmanı,
`2dd4eff` FastAPI sunucusu + web paneli, `8b1e93c` Dalga D (CLI
entrypoint) + test suite'i, `70f194a` Dalga E (`app/notifier.py` +
`--notify-once` + `tests/test_notifier.py`/`tests/test_cli.py`), Dalga F
ise `app/collectors/maintenance_status.py` + `notifier` bakım dalı +
`tests/test_maintenance_status.py`.

## Bakım collector'ı hakkında (Dalga F)

- **Repo taraması TEKRAR YAZILMAZ.** `maintenance_status` kirli/eski repo
  tespitini `git_status.collect` çıktısının üstüne kurar; `git_status`'a
  import edip `find_git_repos`/`repo_summary` fonksiyonlarını çağırmak da
  geçerlidir ama **gereksiz**: `collect` zaten ikisini de çağırıyor. Tek
  ek iş, kirli repoya girip en yeni dosya mtime'ını bulmak
  (`_latest_mtime`, `.git`/`node_modules` gibi `git_status.SKIP_DIR_NAMES`
  dizinleri atlanır, `MAX_SCANNED_ENTRIES` tavanı vardır).
- **`psutil` lazy import edilir** (fonksiyon içinde), tıpkı `psycopg`
  gibi — kurulu değilse yalnızca `stale_processes.error` dolar, `disk` ve
  `stale_git` yine döner. `pyproject.toml`'da bağımlılık olarak durur.
- **Üç alt bölüm birbirini düşürmez.** `collect` her bölümü ayrı
  try/except'te çalıştırır; biri beklenmedik hatada `{"error": ...}`
  olur, diğer ikisi normal döner.
- **Testler izole olmak ZORUNDA.** `shutil.disk_usage` ve `psutil`
  monkeypatch/fake modül ile beslenir; bu konteynerin kendi diski ya da
  süreç listesine asla gidilmez (testler başka bir makinede de
  deterministik kalmalı). Git tarafı istisnadır: `git_status`'un kendi
  deseni gibi `tmp_path` altında kurulan GERÇEK repo + `os.utime` ile
  eski mtime mock'suz kullanılır.
