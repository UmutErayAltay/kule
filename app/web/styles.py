"""Kontrol Kulesi'nin tek CSS sabiti.

Palet cor'un dashboard'undan (src/server/dashboardStyles.ts) ödünç alınan,
validator'dan geçmiş koyu zemin/nötr/durum renkleri ile aynı — ama accent
burada gold değil teal/camgöbeği (#4fb0c9), kule cor'un birebir kopyası
gibi görünmesin diye. Kontrast ve hue-ayrımı elle doğrulandı: accent vs
--bg-elev 6.63:1, accent vs --success hue farkı ~32° (ikisi teal ailesinde
ama karışmayacak kadar ayrık — accent mavi-camgöbeği, success yeşil-teal).

Yerleşim ilkesi (cor-dashboard-tasarim skill'i): sol hizalı, gölgesiz,
hairline kenarlık, radius sadece kart/input'ta, gradient/emoji/ortalama
yok. "sağlıklı" durum nötr renkte kalır, yalnızca sorunlu olan öne çıkar
(kırmızı/sarı) — her şeyi yeşile boyayıp gürültü üretmemek için.
"""
from __future__ import annotations

DASHBOARD_CSS = """
:root {
  --bg: #12161b;
  --bg-elev: #191f26;
  --bg-elev-2: #20272f;
  --border: #2b333d;
  --text: #e8e4d9;
  --text-dim: #a3a8ad;
  --text-faint: #6d747b;

  --accent: #4fb0c9;
  --accent-dim: #3d8a9e;
  --accent-bg: rgba(79, 176, 201, 0.12);

  --success: #45a888;
  --success-bg: rgba(69, 168, 136, 0.12);
  --danger: #c0403a;
  --danger-bg: rgba(192, 64, 58, 0.14);
  --warning: #c9931f;
  --warning-bg: rgba(201, 147, 31, 0.12);

  --radius: 4px;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.45;
}

a { color: var(--accent); }

main {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 20px 48px;
}

/* ---- topbar ---- */

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.topbar h1 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  letter-spacing: 0.01em;
}

.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--text-faint);
  flex: 0 0 auto;
}
.status-dot.ok { background: var(--success); }
.status-dot.warn { background: var(--warning); }
.status-dot.bad { background: var(--danger); }

.status-text {
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-dim);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.conn-badge {
  display: none;
  align-items: center;
  gap: 6px;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--warning);
  background: var(--warning-bg);
  border: 1px solid var(--warning);
  border-radius: var(--radius);
  padding: 3px 8px;
}
.conn-badge.visible { display: inline-flex; }

.updated-at {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-faint);
}

/* ---- eyebrow / section heading ---- */

.eyebrow {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-dim);
  margin: 0 0 10px;
}

section.block {
  margin-bottom: 28px;
}

/* ---- stat grid ---- */

/* Sekiz kaynak (atlas/orkestra eklendi) tek sıraya sığmıyor: 6
   kolonda "ULAŞILAMIYOR" gibi uzun bir değer 24px mono ile taşıyordu.
   Geniş ekranda 3 kolon (üç satır) — her kutunun genişliği korunur,
   tipografi değişmez. */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 28px;
}

@media (max-width: 900px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 520px) {
  .stat-grid { grid-template-columns: 1fr; }
}

.stat-tile {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
}

.stat-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  margin-bottom: 8px;
}

.stat-value {
  font-family: var(--mono);
  font-size: 24px;
  font-weight: 600;
  color: var(--text);
}

.stat-tile.warn .stat-value { color: var(--warning); }
.stat-tile.bad .stat-value { color: var(--danger); }

.stat-sub {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-faint);
  margin-top: 4px;
}

/* ---- card ---- */

.card {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
}

.card + .card { margin-top: 12px; }

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

/* ---- badge ---- */

.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: var(--mono);
  font-size: 11px;
  padding: 2px 7px;
  border-radius: var(--radius);
  border: 1px solid transparent;
  white-space: nowrap;
}
.badge-ok { color: var(--success); background: var(--success-bg); border-color: var(--success); }
.badge-warn { color: var(--warning); background: var(--warning-bg); border-color: var(--warning); }
.badge-bad { color: var(--danger); background: var(--danger-bg); border-color: var(--danger); }
.badge-neutral { color: var(--text-dim); background: var(--bg-elev-2); border-color: var(--border); }

/* ---- key/value list (generic JSON'u göstermek için) ---- */

.kv-list {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 14px;
  margin: 0;
}
.kv-list dt {
  font-size: 12px;
  color: var(--text-dim);
  white-space: nowrap;
}
.kv-list dd {
  margin: 0;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text);
  overflow-wrap: anywhere;
}

.empty-row {
  font-size: 12px;
  color: var(--text-faint);
  padding: 6px 0;
}

/* ---- table ---- */

table.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.data-table th {
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  font-weight: 600;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}

.data-table td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  font-family: var(--mono);
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.data-table tbody tr:hover { background: var(--bg-elev-2); }
.data-table tbody tr:last-child td { border-bottom: none; }

.num-warn { color: var(--warning); }
.num-bad { color: var(--danger); }

/* ---- two-column card row for source cards ---- */

/*
 * Bakım kartı üç tablo içerdiği için en uzun kart; tek başına tam genişlikte
 * bir satır alır (`.card-wide`), geri kalan dört kart 2x2'lik normal ızgarada
 * kalır. Aksi halde bakım kartının yüksekliği ikinci satırı da aşağı çeker,
 * ızgarada boş bir üçüncü satır açılırdı.
 */
.card-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.card-wide { grid-column: 1 / -1; }
@media (max-width: 760px) {
  .card-grid { grid-template-columns: 1fr; }
  .card-wide { grid-column: auto; }
}
"""
