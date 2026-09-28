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
      "collected_at": <epoch saniye>
    }

`maintenance` bir kaynak değil, üç alt bölümden oluşan bir bulgu
listesidir (dalga F) — panelde de öyle gösterilir: alt bölümlerin
`error`'ları kartın üstündeki `maintenance-error` satırında toplanır,
`disk.full` / `stale_processes.items` / `stale_git.items` listeleri
kendi başlıkları altında satır satır basılır, hiçbiri boş değilse
sakin bir "bulgu yok" durumunda kalır.

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
