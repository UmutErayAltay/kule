# kule

Umut'un projelerinin durumunu tek panelde toplayan kontrol kulesi. Git
repolarının durumu, cor (claude-openrouter) proxy sağlığı, BorsaSite
pipeline'ının son çalışması, readbunny veritabanı durumu, Mt3Ui55OS
vault'unun hijyeni ve otomatik bakım uyarıları (dolu disk, unutulmuş
süreçler, eski commitlenmemiş değişiklik) — hepsi tek bir koyu temalı
web panelinde, 30 saniyede bir kendini tazeleyen tek sayfada.

Offline-first değil: kule kendi başına veri üretmez, altı farklı
kaynağa (dosya sistemi, HTTP, Postgres) bağlanıp onları okur. Bir
kaynağa ulaşılamazsa panel çökmez — o kaynağın kartı "erişilemiyor"
gösterir, diğerleri etkilenmez.

## Kurulum

```bash
pip install -e .
cp config.yaml.example config.yaml
```

`config.yaml`'ı kendi yollarınla/URL'lerinle doldur (aşağıda alan
alan açıklandı). `config.yaml` `.gitignore`'da — commit edilmez, her
makinede yerel kalır.

## Çalıştırma

```bash
kule                      # sunucuyu başlatır (127.0.0.1:8790)
kule --port 8000 --reload # ek seçenekler
uvicorn app.main:app      # doğrudan uvicorn da çalışır
```

Panel `http://127.0.0.1:8790` adresinde açılır. `config.yaml` yoksa
sunucu yine de ayağa kalkar, ama `/` ve `/api/summary` 500 döndürüp
`config.yaml.example`'ı kopyalamanı söyler (reverse proxy health-check
gibi şeylerin config'den bağımsız çalışabilmesi için sunucu hiç
başlamamak yerine bu yolu seçiyor).

### Tek seferlik durum bildirimi

```bash
kule --notify-once
```

Sunucuyu **başlatmaz**; altı kaynağı bir kez okur, herhangi biri
erişilemiyorsa ya da hata veriyorsa, ya da otomatik bakım bir şey
bulduysa uyarıyı Telegram'a gönderir ve stdout'a basar. Sorun yoksa
`kule: her şey yolunda` basar ve hiç mesaj göndermez. Her durumda exit
code `0` döner — cron gibi zamanlanmış görevlerde "sorun var" durumu hata
sayılmaz:

```cron
*/15 * * * * /path/to/venv/bin/kule --notify-once
```

Gönderim için `telegram.bot_token`/`chat_id` dolu olmalı; boşsa
Telegram'a hiç istek atılmaz, sadece uyarı stdout'a basılır.

## config.yaml alanları

```yaml
repo_roots:              # git repoları için taranacak kök dizinler
  - ~/Desktop             # ~ ve $ENV_VAR genişletilir
  - ~/Documents           # her kökten en fazla 4 seviye derine inilir

cor:
  base_url: "http://127.0.0.1:8787"   # cor'un çalıştığı adres

borsasite:
  health_url: "https://.../api/health"  # BorsaSite'ın health endpoint'i
  database_url: ""    # opsiyonel — boşsa yalnızca HTTP health kullanılır,
                       # doluysa trade_decisions/predictions tablolarından
                       # son çalışma zamanı da çekilir

readbunny:
  database_url: "postgresql://..."   # readbunny'nin Postgres bağlantısı
                                      # (links tablosundan özet okunur)

vault:
  path: "/path/to/vault"   # vault'un yerel yolu

maintenance:                    # hepsi OPSİYONEL — eksik alan default'a düşer
  disk_threshold_percent: 90    # bu oranın üstünde dolu diskler uyarılır
  stale_process_hours: 6       # bu süreden uzun çalışan süreçler "unutulmuş" sayılır
  stale_git_days: 3             # kirli repoda bu günden eski değişiklik varsa uyarılır
  process_names: [ollama, uvicorn, node]  # izlenecek süreç adı kalıpları
  # disk_paths: []              # boşsa repo_roots, o da boşsa "/" kontrol edilir

telegram:
  bot_token: ""   # `kule --notify-once` bununla uyarı gönderir
  chat_id: ""     # env'den de okunabilir: KULE_TELEGRAM_BOT_TOKEN / KULE_TELEGRAM_CHAT_ID
```

Telegram secret'ları `config.yaml`'a yazmak zorunda değilsin — ortam
değişkeni yaml'daki değerin önüne geçer, secret repo'ya hiç girmez.

## Uçlar

- **`GET /`** — panelin kendisi (tek HTML sayfa, CSS/JS gömülü, build
  adımı yok). Sayfa yüklenince ve her 30 saniyede bir `/api/summary`'yi
  çeker, gelen JSON ile DOM'u doldurur. Bir istek başarısız olursa
  ekranda son bilinen veri kalır, sadece sessiz bir "bağlantı koptu"
  rozeti görünür.
- **`GET /api/summary`** — altı kaynağın JSON özeti:

  ```json
  {
    "git": [{"name", "path", "branch", "dirty_count", "son_commit"}, ...],
    "cor": {"reachable", "health", "dashboard_health", "metrics"},
    "borsasite": {"reachable", "health", "db": {"last_trade_decision", "last_prediction"}},
    "readbunny": {"reachable", "last_updated", "error_count", "pending_count", "total_count"},
    "vault": {"broken_link_count", "orphan_note_count", "open_threads", "total_threads"},
    "maintenance": {
      "disk": {"threshold_percent", "full": [{"path", "percent", "free_gb"}]},
      "stale_processes": {"min_hours", "names", "items": [{"pid", "name", "hours"}]},
      "stale_git": {"min_days", "items": [{"name", "dirty_count", "age_days"}]}
    },
    "collected_at": 1234567890.0
  }
  ```

  Her kaynak kendi try/except'inde izole edilir; biri patlarsa o
  anahtar `{"error": "..."}` olur, diğerleri etkilenmez. Sonuç 60
  saniye boyunca process-içi bellekte önbelleklenir (art arda hızlı
  istekler gerçek collector'ları tekrar tetiklemez).

`kule --notify-once` bu özeti okuyup sorunlu kaynakları kısa bir Türkçe
uyarı metnine çevirir (`app/notifier.py::build_alert_message`): `cor`
erişilemiyor, `vault` hata veriyor, `git`'te 2 repo okunamadı gibi.
`collected_at` bir kaynak değildir, uyarı üretmez.

### Paneldeki bakım kartı

Aynı `maintenance` verisi panelde de görünür: üstteki kutular arasındaki
**"bakım bulgusu"** sayacı (dolu disk + unutulmuş süreç + eski
commitlenmemiş repo toplamı, altında eşikler), altta ise **bakım** kartı —
dolu disk, unutulmuş süreç ve eski commitlenmemiş repo listeleri ayrı
ayrı tablolarda. Hiçbir bulgu yokken sakin bir "her şey yolunda" durumunda
kalır; bir alt bölüm okunamazsa (ör. `psutil` kurulu değilse süreçler)
rozet kırmızıya döner, diğer iki liste normal görünmeye devam eder.
Bulgu, üst bardaki durum noktasını da "dikkat gerektiren nokta var"
durumuna taşır — panel ile Telegram uyarısı aynı bulguyu aynı ciddiyetle
gösterir.

## Otomatik bakım botu (Dalga F)

`maintenance` kaynağı üç şeyi otomatik tespit eder — **yalnızca raporlar**,
hiçbir müdahale yapmaz (süreç öldürülmez, dosya silinmez, git komutu
çalıştırılmaz):

- **Disk doluluğu** — `shutil.disk_usage` ile `disk_paths` (yoksa
  `repo_roots`, o da yoksa `/`) kontrol edilir; `disk_threshold_percent`
  üstü dolu olanlar listelenir.
- **Unutulmuş süreçler** — `psutil` ile `process_names` kalıbına uyan ve
  `stale_process_hours`'tan uzun süredir çalışan süreçler listelenir.
  `psutil` kurulu değilse yalnızca bu bölüm `error` döner.
- **Eski commitlenmemiş değişiklik** — `git_status` zaten repoları taradığı
  için o çıktı yeniden kullanılır: **kirli** olan repoların en son dosya
  değişikliği `stale_git_days`'ten eskiyse listelenir.

`--notify-once` çıktısı bu bulguları diğer kaynaklarla aynı Türkçe uyarı
metnine, her biri ayrı satır olarak ekler:

```
⚠️ kule uyarısı: cor erişilemiyor (kapalı)
disk dolu: / (%95.2)
uzun süredir çalışan süreçler: ollamax2, uvicorn
eski commitlenmemiş değişiklik: kule, borsa
🕐 2026-09-28 10:20
```

Hiçbir bakım bulgusu yoksa (ve hiçbir kaynak sorunlu değilse) mesaj
üretilmez, `kule: her şey yolunda` basılır.
