"""Kontrol Kulesi'nin tek sayfası.

`render_dashboard_html()` veri almaz — sayfa statik bir iskelet döner,
gerçek veriyi `DASHBOARD_JS` sayfa yüklenince `/api/summary`'den çeker ve
DOM'u doldurur (cor'un dashboard'uyla aynı desen: framework yok, build
adımı yok, tek dosyada CSS+JS gömülü). `/api/summary`'nin döneceği JSON
şekli `app/aggregator.py::collect_all`'dan çıkarıldı — her kaynak kendi
anahtarı altında, hatalıysa `{"error": ...}` ile:

    {
      "git": [{"name", "path", "branch", "dirty_count", "son_commit"} | {"name","path","error"}, ...],
      "cor": {"reachable", "health", "dashboard_health", "metrics"} | {"reachable": false, "error"},
      "borsasite": {"reachable", "health", "db": {"last_trade_decision", "last_prediction"} | None, "error"?},
      "readbunny": {"reachable", "last_updated", "error_count", "pending_count", "total_count"} | {"reachable": false, "error"},
      "vault": {"broken_link_count", "orphan_note_count", "open_threads", "total_threads"} | {"error"},
      "maintenance": {"disk": {...}, "stale_processes": {...}, "stale_git": {...}},
      "atlas": {"reachable": true, "son_tarama": <iso|null>, "veri_bayat": <bool|null>,
                "push_bekleyen": <int|null>, "push_bilinmeyen": ..., "bayat_readme": ..., "bulgu_toplam": ...,
                "todo_toplam": ..., "bulgu_onem": {"<onem>": int} | null}
             | {"reachable": false, "error": "<sabit kod>"},
      "orkestra": {"reachable": true, "gorev_toplam": <int|null>, "onay_bekleyen": ...,
                   "basarisiz": ..., "kanitsiz_ya_da_supheli": ...,
                   "gorev_durum": {"<durum>": int} | null,
                   "kota": {"gun": <iso gun|null>, "toplam_istek": int, "uyari_sayisi": int,
                            "veri_var": <bool|null>} | null}
              | {"reachable": false, "error": "<sabit kod>"},
      "bagimlilik": {"reachable": true, "son_tarama": <iso|null>,
                     "repo_sayisi": <int>, "acikli_repo": <int>,
                     "kritik_yuksek": <int>, "toplam_acik": <int>,
                     "denetlenemedi": <int>}
                | {"reachable": false, "error": "<sabit kod>"},
      "collected_at": <epoch saniye>
    }

`maintenance` bir kaynak değil, üç alt bölümden oluşan bir bulgu
listesidir (dalga F) — panelde de öyle gösterilir: alt bölümlerin
`error`'ları kartın üstündeki `maintenance-error` satırında toplanır,
`disk.full` / `stale_processes.items` / `stale_git.items` listeleri
kendi başlıkları altında satır satır basılır, hiçbiri boş değilse
sakin bir "bulgu yok" durumunda kalır.

`atlas`/`orkestra` `durum --json` sözleşmesinden gelen sayılardır
(bakış: `app/collectors/durum_status.py`). Sayı alanları `int` DEĞİLSE
`null` gelir — "bilinmiyor" 0 gibi gösterilmez, panelde "bilinmiyor"
yazar. Erişilemeyen kaynak `{"reachable": false, "error": <sabit kod>}`.
Bu sayaçların (`bayat_readme`, `push_bekleyen`, `basarisiz`) panelde görünmesi
Telegram'a GİTMESİ demek değildir: `notifier.py` yalnızca kaynağa
erişilememesini ve `onay_bekleyen > 0` koşulunu uyarı sayar.

`bagimlilik` bir ALT SÜREÇ DEĞİL, dosya tabanlı bir kaynaktır
(`~/.bagimlilik/son.json`, `app/collectors/bagimlilik_status.py`): kule
raporu okur, doğrular, sayıları verir. Onun `acikli_repo`/`kritik_yuksek`
sayaçları da sürekli >0 olabilen inceleme bulgularıdır — panelde görünür,
Telegram'a GİTMEZ.

DOM kimlikleri (id) `DASHBOARD_JS` ile birebir eşleşir — biri değişirse
diğeri de değişmeli, aksi halde sayfa sessizce boş kalır (JS elementi
bulamayınca hiçbir hata fırlatmaz, `qs()` null döner ve atlanır).
"""
from __future__ import annotations

from app.web.script import DASHBOARD_JS
from app.web.styles import DASHBOARD_CSS

_HTML_TOP = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kontrol Kulesi</title>
<style>"""

_HTML_MID = """</style>
</head>
<body>
<main>

<div class="topbar">
  <div class="topbar-left">
    <span class="status-dot" id="status-dot"></span>
    <h1>Kontrol Kulesi</h1>
    <span class="status-text" id="status-text">yükleniyor…</span>
  </div>
  <div class="topbar-right">
    <span class="conn-badge" id="conn-badge">bağlantı koptu</span>
    <span class="updated-at" id="updated-at"></span>
  </div>
</div>

<nav class="tabs" id="tabs" role="tablist" aria-label="Paneller">
  <button class="tab tab-active" id="tab-kule" data-tab="kule" role="tab" type="button" aria-selected="true">Kule</button>
  <button class="tab" id="tab-atlas" data-tab="atlas" role="tab" type="button" aria-selected="false"><span class="tab-dot" id="tab-dot-atlas"></span>atlas</button>
  <button class="tab" id="tab-orkestra" data-tab="orkestra" role="tab" type="button" aria-selected="false"><span class="tab-dot" id="tab-dot-orkestra"></span>orkestra</button>
  <button class="tab" id="tab-harita" data-tab="harita" role="tab" type="button" aria-selected="false"><span class="tab-dot" id="tab-dot-harita"></span>harita</button>
  <button class="tab" id="tab-liman" data-tab="liman" role="tab" type="button" aria-selected="false"><span class="tab-dot" id="tab-dot-liman"></span>liman</button>
</nav>

<div class="tab-panel" id="panel-kule" role="tabpanel">

<section class="block">
  <div class="stat-grid">
    <div class="stat-tile" id="stat-dirty-tile">
      <div class="stat-label">Kirli repo</div>
      <div class="stat-value" id="stat-dirty-value">—</div>
      <div class="stat-sub" id="stat-dirty-sub"></div>
    </div>
    <div class="stat-tile" id="stat-cor-tile">
      <div class="stat-label">cor sağlık</div>
      <div class="stat-value" id="stat-cor-value">—</div>
      <div class="stat-sub" id="stat-cor-sub"></div>
    </div>
    <div class="stat-tile" id="stat-borsasite-tile">
      <div class="stat-label">BorsaSite son pipeline</div>
      <div class="stat-value" id="stat-borsasite-value">—</div>
      <div class="stat-sub" id="stat-borsasite-sub"></div>
    </div>
    <div class="stat-tile" id="stat-readbunny-tile">
      <div class="stat-label">readbunny hatalı link</div>
      <div class="stat-value" id="stat-readbunny-value">—</div>
      <div class="stat-sub" id="stat-readbunny-sub"></div>
    </div>
    <div class="stat-tile" id="stat-vault-tile">
      <div class="stat-label">vault açık hikâye</div>
      <div class="stat-value" id="stat-vault-value">—</div>
      <div class="stat-sub" id="stat-vault-sub"></div>
    </div>
    <div class="stat-tile" id="stat-maintenance-tile">
      <div class="stat-label">bakım bulgusu</div>
      <div class="stat-value" id="stat-maintenance-value">—</div>
      <div class="stat-sub" id="stat-maintenance-sub"></div>
    </div>
    <div class="stat-tile" id="stat-atlas-tile">
      <div class="stat-label">atlas bulgu</div>
      <div class="stat-value" id="stat-atlas-value">—</div>
      <div class="stat-sub" id="stat-atlas-sub"></div>
    </div>
    <div class="stat-tile" id="stat-orkestra-tile">
      <div class="stat-label">orkestra onay bekleyen</div>
      <div class="stat-value" id="stat-orkestra-value">—</div>
      <div class="stat-sub" id="stat-orkestra-sub"></div>
    </div>
    <div class="stat-tile" id="stat-bagimlilik-tile">
      <div class="stat-label">bağımlılık açık repo</div>
      <div class="stat-value" id="stat-bagimlilik-value">—</div>
      <div class="stat-sub" id="stat-bagimlilik-sub"></div>
    </div>
  </div>
</section>

<section class="block">
  <p class="eyebrow">Repolar</p>
  <div class="card">
    <table class="data-table">
      <thead>
        <tr><th>isim</th><th>branch</th><th>dirty</th><th>son commit</th></tr>
      </thead>
      <tbody id="repo-table-body">
        <tr><td colspan="4" class="empty-row">yükleniyor…</td></tr>
      </tbody>
    </table>
  </div>
</section>

<section class="block">
  <div class="card-grid">

    <div>
      <p class="eyebrow">cor (claude-openrouter)</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="cor-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="cor-error"></div>
        <p class="eyebrow" style="margin-top:10px;">health</p>
        <dl class="kv-list" id="cor-health-kv"><div class="empty-row">yükleniyor…</div></dl>
        <p class="eyebrow" style="margin-top:10px;">metrics</p>
        <dl class="kv-list" id="cor-metrics-kv"><div class="empty-row">yükleniyor…</div></dl>
      </div>
    </div>

    <div>
      <p class="eyebrow">BorsaSite</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="borsasite-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="borsasite-error"></div>
        <dl class="kv-list">
          <dt>son trade_decision</dt><dd id="borsasite-last-trade">—</dd>
          <dt>son prediction</dt><dd id="borsasite-last-pred">—</dd>
        </dl>
        <p class="eyebrow" style="margin-top:10px;">health</p>
        <dl class="kv-list" id="borsasite-health-kv"><div class="empty-row">yükleniyor…</div></dl>
      </div>
    </div>

    <div>
      <p class="eyebrow">readbunny</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="readbunny-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="readbunny-error"></div>
        <dl class="kv-list">
          <dt>toplam link</dt><dd id="readbunny-total">—</dd>
          <dt>bekleyen</dt><dd id="readbunny-pending">—</dd>
          <dt>hatalı</dt><dd id="readbunny-errors">—</dd>
          <dt>son güncelleme</dt><dd id="readbunny-updated">—</dd>
        </dl>
      </div>
    </div>

    <div>
      <p class="eyebrow">vault (Mt3Ui55OS)</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="vault-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="vault-error"></div>
        <dl class="kv-list">
          <dt>kırık link</dt><dd id="vault-broken">—</dd>
          <dt>yetim not</dt><dd id="vault-orphan">—</dd>
          <dt>açık hikâye</dt><dd id="vault-open-threads">—</dd>
          <dt>toplam hikâye</dt><dd id="vault-total-threads">—</dd>
        </dl>
      </div>
    </div>

    <div>
      <p class="eyebrow">atlas</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="atlas-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="atlas-error"></div>
        <dl class="kv-list">
          <dt>push bekleyen</dt><dd id="atlas-push-bekleyen">—</dd>
          <dt>push bilinmeyen</dt><dd id="atlas-push-bilinmeyen">—</dd>
          <dt>bayat README</dt><dd id="atlas-bayat-readme">—</dd>
          <dt>toplam bulgu</dt><dd id="atlas-bulgu-toplam">—</dd>
          <dt>toplam todo</dt><dd id="atlas-todo-toplam">—</dd>
          <dt>son tarama</dt><dd id="atlas-son-tarama">—</dd>
        </dl>
        <p class="eyebrow" style="margin-top:10px;">bulgu (önem)</p>
        <dl class="kv-list" id="atlas-bulgu-onem-kv"><div class="empty-row">yükleniyor…</div></dl>
      </div>
    </div>

    <div>
      <p class="eyebrow">orkestra</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="orkestra-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="orkestra-error"></div>
        <dl class="kv-list">
          <dt>toplam görev</dt><dd id="orkestra-gorev-toplam">—</dd>
          <dt>onay bekleyen</dt><dd id="orkestra-onay-bekleyen">—</dd>
          <dt>başarısız</dt><dd id="orkestra-basarisiz">—</dd>
          <dt>kanıtsız/şüpheli</dt><dd id="orkestra-kanitsiz">—</dd>
        </dl>
        <p class="eyebrow" style="margin-top:10px;">görev (durum)</p>
        <dl class="kv-list" id="orkestra-gorev-durum-kv"><div class="empty-row">yükleniyor…</div></dl>
        <p class="eyebrow" style="margin-top:10px;">kota</p>
        <dl class="kv-list" id="orkestra-kota-kv"><div class="empty-row">yükleniyor…</div></dl>
      </div>
    </div>

    <div class="card-wide">
      <p class="eyebrow">bağımlılık</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="bagimlilik-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="bagimlilik-error"></div>
        <dl class="kv-list">
          <dt>taranan repo</dt><dd id="bagimlilik-repo-sayisi">—</dd>
          <dt>açık repo</dt><dd id="bagimlilik-acikli-repo">—</dd>
          <dt>kritik + yüksek</dt><dd id="bagimlilik-kritik-yuksek">—</dd>
          <dt>toplam açık</dt><dd id="bagimlilik-toplam-acik">—</dd>
          <dt>denetlenemedi</dt><dd id="bagimlilik-denetlenemedi">—</dd>
          <dt>son tarama</dt><dd id="bagimlilik-son-tarama">—</dd>
        </dl>
      </div>
    </div>

    <div class="card-wide">
      <p class="eyebrow">bakım (raporlar, müdahale etmez)</p>
      <div class="card">
        <div class="card-head">
          <span class="badge badge-neutral" id="maintenance-badge">bekliyor</span>
        </div>
        <div class="stat-sub num-bad" id="maintenance-error"></div>
        <p class="eyebrow" style="margin-top:10px;">dolu disk</p>
        <table class="data-table">
          <thead>
            <tr><th>yol</th><th>doluluk</th><th>boş</th></tr>
          </thead>
          <tbody id="maintenance-disk-body">
            <tr><td colspan="3" class="empty-row">yükleniyor…</td></tr>
          </tbody>
        </table>
        <p class="eyebrow" style="margin-top:10px;">unutulmuş süreç</p>
        <table class="data-table">
          <thead>
            <tr><th>ad</th><th>pid</th><th>ne kadar</th></tr>
          </thead>
          <tbody id="maintenance-process-body">
            <tr><td colspan="3" class="empty-row">yükleniyor…</td></tr>
          </tbody>
        </table>
        <p class="eyebrow" style="margin-top:10px;">eski commitlenmemiş repo</p>
        <table class="data-table">
          <thead>
            <tr><th>isim</th><th>değişiklik</th><th>ne kadar eski</th></tr>
          </thead>
          <tbody id="maintenance-git-body">
            <tr><td colspan="3" class="empty-row">yükleniyor…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</section>

<section class="block">
  <p class="eyebrow">Araçlar</p>
  <div class="tool-grid">

    <div class="card tool-card">
      <div class="card-head">
        <span class="badge badge-neutral" id="tool-badge-atlas">bekliyor</span>
        <span class="tool-name">atlas</span>
      </div>
      <dl class="kv-list">
        <dt>port</dt><dd id="tool-port-atlas">—</dd>
        <dt>pid</dt><dd id="tool-pid-atlas">—</dd>
        <dt>veri</dt><dd id="tool-hazir-atlas">—</dd>
      </dl>
    </div>

    <div class="card tool-card">
      <div class="card-head">
        <span class="badge badge-neutral" id="tool-badge-orkestra">bekliyor</span>
        <span class="tool-name">orkestra</span>
      </div>
      <dl class="kv-list">
        <dt>port</dt><dd id="tool-port-orkestra">—</dd>
        <dt>pid</dt><dd id="tool-pid-orkestra">—</dd>
        <dt>veri</dt><dd id="tool-hazir-orkestra">—</dd>
      </dl>
    </div>

    <div class="card tool-card">
      <div class="card-head">
        <span class="badge badge-neutral" id="tool-badge-harita">bekliyor</span>
        <span class="tool-name">harita</span>
      </div>
      <dl class="kv-list">
        <dt>port</dt><dd id="tool-port-harita">—</dd>
        <dt>pid</dt><dd id="tool-pid-harita">—</dd>
        <dt>veri</dt><dd id="tool-hazir-harita">—</dd>
      </dl>
    </div>

    <div class="card tool-card">
      <div class="card-head">
        <span class="badge badge-neutral" id="tool-badge-liman">bekliyor</span>
        <span class="tool-name">liman</span>
      </div>
      <dl class="kv-list">
        <dt>port</dt><dd id="tool-port-liman">—</dd>
        <dt>pid</dt><dd id="tool-pid-liman">—</dd>
        <dt>veri</dt><dd id="tool-hazir-liman">—</dd>
      </dl>
    </div>

  </div>
  <div class="stat-sub num-bad" id="tools-error"></div>
</section>

</div>

<section class="tab-panel tool-panel" id="panel-atlas" role="tabpanel" hidden>
  <div class="tool-bar">
    <span class="tool-state" id="tool-state-atlas">kapalı</span>
    <span class="tool-bar-actions">
      <button class="btn" id="tool-open-atlas" type="button" hidden>Yeni sekmede aç</button>
      <button class="btn" id="tool-restart-atlas" type="button">Yeniden başlat</button>
      <button class="btn btn-danger" id="tool-stop-atlas" type="button">Şimdi kapat</button>
    </span>
  </div>
  <div class="tool-notice" id="tool-notice-atlas" hidden></div>
  <div class="tool-frame-host" id="tool-frame-atlas"></div>
</section>

<section class="tab-panel tool-panel" id="panel-orkestra" role="tabpanel" hidden>
  <div class="tool-bar">
    <span class="tool-state" id="tool-state-orkestra">kapalı</span>
    <span class="tool-bar-actions">
      <button class="btn" id="tool-open-orkestra" type="button" hidden>Yeni sekmede aç</button>
      <button class="btn" id="tool-restart-orkestra" type="button">Yeniden başlat</button>
      <button class="btn btn-danger" id="tool-stop-orkestra" type="button">Şimdi kapat</button>
    </span>
  </div>
  <div class="tool-notice" id="tool-notice-orkestra" hidden></div>
  <div class="tool-frame-host" id="tool-frame-orkestra"></div>
</section>

<section class="tab-panel tool-panel" id="panel-harita" role="tabpanel" hidden>
  <div class="tool-bar">
    <span class="tool-state" id="tool-state-harita">kapalı</span>
    <span class="tool-bar-actions">
      <button class="btn" id="tool-open-harita" type="button" hidden>Yeni sekmede aç</button>
      <button class="btn" id="tool-restart-harita" type="button">Yeniden başlat</button>
      <button class="btn btn-danger" id="tool-stop-harita" type="button">Şimdi kapat</button>
    </span>
  </div>
  <div class="tool-notice" id="tool-notice-harita" hidden></div>
  <div class="tool-frame-host" id="tool-frame-harita"></div>
</section>

<section class="tab-panel tool-panel" id="panel-liman" role="tabpanel" hidden>
  <div class="tool-bar">
    <span class="tool-state" id="tool-state-liman">kapalı</span>
    <span class="tool-bar-actions">
      <button class="btn" id="tool-open-liman" type="button" hidden>Yeni sekmede aç</button>
      <button class="btn" id="tool-restart-liman" type="button">Yeniden başlat</button>
      <button class="btn btn-danger" id="tool-stop-liman" type="button">Şimdi kapat</button>
    </span>
  </div>
  <div class="tool-notice" id="tool-notice-liman" hidden></div>
  <div class="tool-frame-host" id="tool-frame-liman"></div>
</section>

</main>
<script>"""

_HTML_BOTTOM = """</script>
</body>
</html>
"""


def render_dashboard_html() -> str:
    """Kontrol Kulesi'nin tam HTML sayfasını döner. Argüman almaz — veri
    içermez, sayfa yüklenince istemci `/api/summary`'yi kendisi çeker."""
    return _HTML_TOP + DASHBOARD_CSS + _HTML_MID + DASHBOARD_JS + _HTML_BOTTOM
