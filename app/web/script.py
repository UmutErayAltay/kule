"""Kontrol Kulesi'nin tek JS sabiti.

Sayfa yüklenince ve her 30 saniyede bir `/api/summary`'yi çeker; şekli
`app/aggregator.py::collect_all`'ın döndürdüğü dict ile birebir aynı
varsayılır: {git: [...], cor: {...}, borsasite: {...}, readbunny: {...},
vault: {...}, maintenance: {...}, atlas: {...}, orkestra: {...},
harita: {...}, collected_at: <epoch saniye>}. Her
collector kendi `{"error": ...}` ile izole düştüğü için burada da her
okuma "alan yoksa/obje değilse boş göster" ilkesiyle savunmalı yazılıyor
— backend'in tam şeklini bilmeden (henüz ayrı bir ajan yazıyor) kırılgan
olmamak için. `maintenance` alt bölümleri de aynı izolasyonu taşır:
biri düşse bile diğer iki listenin verisi ekranda kalır.

`atlas`/`orkestra`/`harita` sayı alanlarında `null` = BİLİNMİYOR'dur ve
`count()` ile "bilinmiyor" yazılır — `|| 0` gibi bir kısayol sessizce sıfır
uydurur ve sözleşmenin "0 DEĞİL" kuralını panelde ihlal ederdi.

fetch başarısız olursa (`.catch`) son bilinen veri DOM'da kalır, sadece
sessiz bir "bağlantı koptu" rozeti görünür hale gelir — hiçbir alan
sıfırlanmaz/temizlenmez.
"""
from __future__ import annotations

DASHBOARD_JS = """
(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var STALE_MINUTES = 24 * 60; // borsasite pipeline bu kadar eskiyse uyar

  function qs(id) { return document.getElementById(id); }

  function setText(id, value) {
    var el = qs(id);
    if (el) el.textContent = String(value);
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    try {
      return d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
    } catch (e) {
      return d.toISOString();
    }
  }

  function fmtRelative(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    var mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 0) return "az önce";
    if (mins < 1) return "az önce";
    if (mins < 60) return mins + " dk önce";
    var hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + " sa önce";
    var days = Math.round(hrs / 24);
    return days + " gün önce";
  }

  function isStale(iso, thresholdMins) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return false;
    var mins = (Date.now() - d.getTime()) / 60000;
    return mins > thresholdMins;
  }

  function setBadge(el, kind, text) {
    if (!el) return;
    el.className = "badge badge-" + kind;
    el.textContent = text;
  }

  function renderKv(el, obj, skipKeys) {
    if (!el) return;
    skipKeys = skipKeys || [];
    el.innerHTML = "";
    var isPlainObject = obj && typeof obj === "object" && !Array.isArray(obj);
    var keys = isPlainObject ? Object.keys(obj).filter(function (k) { return skipKeys.indexOf(k) === -1; }) : [];
    if (!isPlainObject || keys.length === 0) {
      var p = document.createElement("div");
      p.className = "empty-row";
      p.textContent = "veri yok";
      el.appendChild(p);
      return;
    }
    keys.forEach(function (k) {
      var dt = document.createElement("dt");
      dt.textContent = k;
      var dd = document.createElement("dd");
      var v = obj[k];
      if (v === null || v === undefined) v = "—";
      else if (typeof v === "object") v = JSON.stringify(v);
      dd.textContent = String(v);
      el.appendChild(dt);
      el.appendChild(dd);
    });
  }

  function fmtPercent(v) {
    if (v === null || v === undefined || typeof v !== "number" || isNaN(v)) return "—";
    return "%" + Math.round(v * 100);
  }

  function renderCorMetricsSummary(el, metrics) {
    if (!el) return;
    el.innerHTML = "";
    var totals = metrics && typeof metrics === "object" ? metrics.totals : null;
    var rows = [
      ["toplam istek", totals && typeof totals.total === "number" ? String(totals.total) : "—"],
      ["başarı oranı", totals ? fmtPercent(totals.successRate) : "—"],
      ["1s hata oranı", totals ? fmtPercent(totals.errorRate1h) : "—"]
    ];
    rows.forEach(function (row) {
      var dt = document.createElement("dt");
      dt.textContent = row[0];
      var dd = document.createElement("dd");
      dd.textContent = row[1];
      el.appendChild(dt);
      el.appendChild(dd);
    });
  }

  function setStatTile(prefix, value, kind, sub) {
    var tile = qs(prefix + "-tile");
    var valueEl = qs(prefix + "-value");
    var subEl = qs(prefix + "-sub");
    if (valueEl) valueEl.textContent = value;
    if (subEl) subEl.textContent = sub || "";
    if (tile) {
      tile.classList.remove("warn", "bad");
      if (kind === "warn") tile.classList.add("warn");
      if (kind === "bad") tile.classList.add("bad");
    }
  }

  function renderStats(data) {
    var git = Array.isArray(data.git) ? data.git : [];
    var validRepos = git.filter(function (r) { return !r.error; });
    var dirtyChanges = validRepos.reduce(function (sum, r) { return sum + (r.dirty_count || 0); }, 0);
    var dirtyRepoCount = validRepos.filter(function (r) { return (r.dirty_count || 0) > 0; }).length;
    setStatTile(
      "stat-dirty",
      String(dirtyRepoCount),
      dirtyRepoCount > 0 ? "warn" : "ok",
      dirtyChanges + " değişiklik / " + validRepos.length + " repo"
    );

    var cor = data.cor || {};
    if (cor.reachable) {
      setStatTile("stat-cor", "ÇALIŞIYOR", "ok", "");
    } else {
      setStatTile("stat-cor", "ULAŞILAMIYOR", "bad", cor.error ? String(cor.error).slice(0, 48) : "");
    }

    var bs = data.borsasite || {};
    var lastTimes = [];
    if (bs.db) {
      if (bs.db.last_trade_decision) lastTimes.push(bs.db.last_trade_decision);
      if (bs.db.last_prediction) lastTimes.push(bs.db.last_prediction);
    }
    lastTimes.sort();
    var lastPipeline = lastTimes.length ? lastTimes[lastTimes.length - 1] : null;
    if (!bs.reachable) {
      setStatTile("stat-borsasite", "ULAŞILAMIYOR", "bad", "");
    } else if (!lastPipeline) {
      setStatTile("stat-borsasite", "—", "warn", "pipeline verisi yok");
    } else {
      var stale = isStale(lastPipeline, STALE_MINUTES);
      setStatTile("stat-borsasite", fmtRelative(lastPipeline) || fmtDate(lastPipeline), stale ? "warn" : "ok", fmtDate(lastPipeline));
    }

    var rb = data.readbunny || {};
    if (!rb.reachable) {
      setStatTile("stat-readbunny", "ULAŞILAMIYOR", "bad", "");
    } else {
      var errCount = rb.error_count || 0;
      setStatTile(
        "stat-readbunny",
        String(errCount),
        errCount > 0 ? "warn" : "ok",
        (rb.pending_count || 0) + " bekleyen / " + (rb.total_count || 0) + " toplam"
      );
    }

    var vault = data.vault || {};
    if (vault.error) {
      setStatTile("stat-vault", "—", "bad", String(vault.error).slice(0, 48));
    } else {
      setStatTile("stat-vault", String(vault.open_threads || 0), "ok", (vault.total_threads || 0) + " toplam hikâye");
    }

    var mt = data.maintenance || {};
    if (!maintenanceKnown(mt)) {
      setStatTile("stat-maintenance", "—", "warn", "veri yok");
    } else {
      var mtErrors = maintenanceErrors(mt);
      var mtFindings = maintenanceFindings(mt);
      if (mtErrors.length) {
        setStatTile("stat-maintenance", "—", "bad", mtErrors[0].slice(0, 48));
      } else {
        setStatTile(
          "stat-maintenance",
          String(mtFindings),
          mtFindings > 0 ? "warn" : "ok",
          mtFindings > 0
            ? mtFindings + " bulgu · " + fmtMaintenanceThresholds(mt)
            : "eşiklerin altında · " + fmtMaintenanceThresholds(mt)
        );
      }
    }

    // atlas: veri BAYAT ise sayılar güvenilmezdir — bu yüzden kutuyu sarı
    // değil kırmızıya çeviriyoruz (bir "eski" bilgi, sıfırdan kötüdür).
    var atlas = isPlainObject(data.atlas) ? data.atlas : {};
    if (atlas.reachable !== true) {
      setStatTile("stat-atlas", "ULAŞILAMIYOR", "bad", atlas.error ? String(atlas.error).slice(0, 48) : "");
    } else {
      setStatTile(
        "stat-atlas",
        count(atlas.bulgu_toplam),
        atlas.veri_bayat === true ? "warn" : "ok",
        atlas.veri_bayat === true
          ? "veri bayat · " + fmtDate(atlas.son_tarama)
          : (atlas.kirli_repo != null ? atlas.kirli_repo + " kirli / " + count(atlas.repo_sayisi) + " repo" : "")
      );
    }

    // orkestra: onay bekleyen görev bir insanın işi beklediği anlamına gelir
    // — dikkat, ama arıza değil; başarısız görev ise akışta çökmedir.
    var ork = isPlainObject(data.orkestra) ? data.orkestra : {};
    if (ork.reachable !== true) {
      setStatTile("stat-orkestra", "ULAŞILAMIYOR", "bad", ork.error ? String(ork.error).slice(0, 48) : "");
    } else {
      var bekleyen = ork.onay_bekleyen;
      var basarisiz = ork.basarisiz;
      // `basarisiz` birikir (iptal edilmedikçe düşmez): renge/üst çubuğa
      // girerse bir eski görev paneli sonsuza dek kırmızı tutar. Sayı
      // altyazıda görünür, ciddiyet yalnızca onay bekleyenden gelir.
      var orkKind = (typeof bekleyen === "number" && bekleyen > 0) ? "warn" : "ok";
      setStatTile(
        "stat-orkestra",
        count(bekleyen),
        orkKind,
        count(ork.gorev_toplam) + " görev" + (ork.basarisiz != null ? " · " + count(ork.basarisiz) + " başarısız" : "")
      );
    }

    // harita: indeks bayatsa kırık link/şüpheli not sayıları güvenilmezdir.
    var harita = isPlainObject(data.harita) ? data.harita : {};
    if (harita.reachable !== true) {
      setStatTile("stat-harita", "ULAŞILAMIYOR", "bad", harita.error ? String(harita.error).slice(0, 48) : "");
    } else {
      setStatTile(
        "stat-harita",
        count(harita.kirik_link),
        harita.indeks_bayat === true ? "warn" : "ok",
        harita.indeks_bayat === true
          ? "indeks bayat · " + fmtDate(harita.son_indeks)
          : count(harita.yetim_not) + " yetim not"
      );
    }
  }

  function renderRepoTable(git) {
    var tbody = qs("repo-table-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    var repos = Array.isArray(git) ? git : [];
    if (repos.length === 0) {
      var trEmpty = document.createElement("tr");
      var tdEmpty = document.createElement("td");
      tdEmpty.colSpan = 4;
      tdEmpty.className = "empty-row";
      tdEmpty.textContent = "repo bulunamadı";
      trEmpty.appendChild(tdEmpty);
      tbody.appendChild(trEmpty);
      return;
    }
    repos.forEach(function (r) {
      var tr = document.createElement("tr");
      if (r.error) {
        var tdName = document.createElement("td");
        tdName.textContent = r.name || "—";
        var tdErr = document.createElement("td");
        tdErr.colSpan = 3;
        tdErr.className = "num-bad";
        tdErr.textContent = "hata: " + r.error;
        tr.appendChild(tdName);
        tr.appendChild(tdErr);
        tbody.appendChild(tr);
        return;
      }
      var tdName2 = document.createElement("td");
      tdName2.textContent = r.name || "—";
      var tdBranch = document.createElement("td");
      tdBranch.textContent = r.branch || "—";
      var tdDirty = document.createElement("td");
      tdDirty.textContent = r.dirty_count != null ? String(r.dirty_count) : "—";
      if ((r.dirty_count || 0) > 0) tdDirty.className = "num-warn";
      var tdCommit = document.createElement("td");
      tdCommit.textContent = r.son_commit || "—";
      tr.appendChild(tdName2);
      tr.appendChild(tdBranch);
      tr.appendChild(tdDirty);
      tr.appendChild(tdCommit);
      tbody.appendChild(tr);
    });
  }

  function renderTable(id, colSpan, rows, emptyText) {
    var tbody = qs(id);
    if (!tbody) return;
    tbody.innerHTML = "";
    if (!rows.length) {
      var trEmpty = document.createElement("tr");
      var tdEmpty = document.createElement("td");
      tdEmpty.colSpan = colSpan;
      tdEmpty.className = "empty-row";
      tdEmpty.textContent = emptyText;
      trEmpty.appendChild(tdEmpty);
      tbody.appendChild(trEmpty);
      return;
    }
    rows.forEach(function (cells) {
      var tr = document.createElement("tr");
      cells.forEach(function (cell) {
        var td = document.createElement("td");
        td.textContent = cell;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function listOrEmpty(value) {
    return Array.isArray(value) ? value : [];
  }

  function num(value) {
    return typeof value === "number" && !isNaN(value) ? String(value) : "—";
  }

  // ---- durum --json kaynakları (atlas / orkestra / harita) -------------
  //
  // Sözleşme "bilinmeyen için null (0 DEĞİL)" diyor. Backend de int olmayan
  // sayıyı null'a çeviriyor, ama arada bir yerde `|| 0` yazmak sessizce
  // "ölçtüm, sıfır" anlamına gelen bir sayı uydurur — panelde de
  // notifier'da da yanlış bir güvence izlenimi yaratırdı. Bu yüzden
  // `count()` null'ı AYRI bir metne çevirir, `|| 0` hiçbir yerde kullanılmaz.

  var UNKNOWN = "bilinmiyor";

  function count(value) {
    // backend int>=0 ya da null döner; null (ve JSON'daki true/false/"3"
    // gibi kayıp değerler) "bilinmiyor" olur, 0 DEĞİL.
    return typeof value === "number" && isFinite(value) ? String(value) : UNKNOWN;
  }

  // Not: `atlas`'ın `bulgu_onem`/`orkestra`'nın `gorev_durum` haritaları
  // KISA SABİT ETİKET -> sayı sözlüğüdür (kaynak enum'ları). Panelde
  // key/value listesi olarak basılır; `renderKv` zaten yalnızca düz
  // nesneleri kabul eder, diziyi JSON string'i olarak basardı.

  function setCountText(id, value) {
    setText(id, count(value));
  }

  function renderAtlas(atlas) {
    atlas = isPlainObject(atlas) ? atlas : {};
    var reachable = atlas.reachable === true;
    setBadge(qs("atlas-badge"), reachable ? "ok" : "bad", reachable ? "okundu" : "erişilemiyor");
    setText("atlas-error", atlas.error ? String(atlas.error) : "");
    setCountText("atlas-repo-sayisi", atlas.repo_sayisi);
    setCountText("atlas-kirli-repo", atlas.kirli_repo);
    setCountText("atlas-push-bekleyen", atlas.push_bekleyen);
    setCountText("atlas-push-bilinmeyen", atlas.push_bilinmeyen);
    setCountText("atlas-bayat-readme", atlas.bayat_readme);
    setCountText("atlas-bulgu-toplam", atlas.bulgu_toplam);
    setCountText("atlas-todo-toplam", atlas.todo_toplam);
    setText("atlas-son-tarama", atlas.son_tarama ? fmtDate(atlas.son_tarama) : UNKNOWN);
    renderKv(qs("atlas-bulgu-onem-kv"), atlas.bulgu_onem);
  }

  function renderOrkestra(orkestra) {
    orkestra = isPlainObject(orkestra) ? orkestra : {};
    var reachable = orkestra.reachable === true;
    setBadge(qs("orkestra-badge"), reachable ? "ok" : "bad", reachable ? "okundu" : "erişilemiyor");
    setText("orkestra-error", orkestra.error ? String(orkestra.error) : "");
    setCountText("orkestra-gorev-toplam", orkestra.gorev_toplam);
    setCountText("orkestra-onay-bekleyen", orkestra.onay_bekleyen);
    setCountText("orkestra-basarisiz", orkestra.basarisiz);
    setCountText("orkestra-kanitsiz", orkestra.kanitsiz_ya_da_supheli);
    renderKv(qs("orkestra-gorev-durum-kv"), orkestra.gorev_durum);
    renderKv(qs("orkestra-kota-kv"), orkestra.kota);
  }

  function renderHarita(harita) {
    harita = isPlainObject(harita) ? harita : {};
    var reachable = harita.reachable === true;
    setBadge(qs("harita-badge"), reachable ? "ok" : "bad", reachable ? "okundu" : "erişilemiyor");
    setText("harita-error", harita.error ? String(harita.error) : "");
    setCountText("harita-not-sayisi", harita.not_sayisi);
    setCountText("harita-kirik-link", harita.kirik_link);
    setCountText("harita-yetim-not", harita.yetim_not);
    setCountText("harita-tutarlilik-uyari", harita.tutarlilik_uyari);
    setText("harita-son-indeks", harita.son_indeks ? fmtDate(harita.son_indeks) : UNKNOWN);
  }

  function renderCor(cor) {
    cor = cor || {};
    setBadge(qs("cor-badge"), cor.reachable ? "ok" : "bad", cor.reachable ? "erişilebilir" : "erişilemiyor");
    setText("cor-error", cor.error ? String(cor.error) : "");
    renderKv(qs("cor-health-kv"), cor.health);
    renderCorMetricsSummary(qs("cor-metrics-kv"), cor.metrics);
  }

  function renderBorsasite(bs) {
    bs = bs || {};
    setBadge(qs("borsasite-badge"), bs.reachable ? "ok" : "bad", bs.reachable ? "erişilebilir" : "erişilemiyor");
    setText("borsasite-error", bs.error ? String(bs.error) : "");
    renderKv(qs("borsasite-health-kv"), bs.health);
    setText("borsasite-last-trade", bs.db && bs.db.last_trade_decision ? fmtDate(bs.db.last_trade_decision) : "—");
    setText("borsasite-last-pred", bs.db && bs.db.last_prediction ? fmtDate(bs.db.last_prediction) : "—");
  }

  function renderReadbunny(rb) {
    rb = rb || {};
    setBadge(qs("readbunny-badge"), rb.reachable ? "ok" : "bad", rb.reachable ? "erişilebilir" : "erişilemiyor");
    setText("readbunny-error", rb.error ? String(rb.error) : "");
    setText("readbunny-total", rb.total_count != null ? rb.total_count : "—");
    setText("readbunny-pending", rb.pending_count != null ? rb.pending_count : "—");
    setText("readbunny-errors", rb.error_count != null ? rb.error_count : "—");
    setText("readbunny-updated", rb.last_updated ? fmtDate(rb.last_updated) : "—");
  }

  function renderVault(vault) {
    vault = vault || {};
    setBadge(qs("vault-badge"), vault.error ? "bad" : "ok", vault.error ? "hata" : "okundu");
    setText("vault-error", vault.error ? String(vault.error) : "");
    setText("vault-broken", vault.broken_link_count != null ? vault.broken_link_count : "—");
    setText("vault-orphan", vault.orphan_note_count != null ? vault.orphan_note_count : "—");
    setText("vault-open-threads", vault.open_threads != null ? vault.open_threads : "—");
    setText("vault-total-threads", vault.total_threads != null ? vault.total_threads : "—");
  }

  // ---- bakım (dalga F) -------------------------------------------------
  // `maintenance` bir kaynak değil, üç alt bölümden oluşan bir bulgu
  // listesi — bu yüzden her bölüm ayrı okunur ve biri düşse bile diğer
  // ikisi ekranda kalır (backend'deki izolasyonun panel karşılığı).

  function maintenanceSection(mt, key) {
    return isPlainObject(mt) && isPlainObject(mt[key]) ? mt[key] : null;
  }

  function maintenanceItems(mt, key) {
    var section = maintenanceSection(mt, key);
    return section ? listOrEmpty(section.items).filter(isPlainObject) : [];
  }

  // `maintenance` beklenen üç alt bölümü taşımalı. Alan hiç yoksa, yanlış
  // tipteyse ya da aggregator'ın izolasyonundan gelen {"error": ...} ise veri
  // YOKTUR — "eşiklerin altında" demek olmayan veriyi varmış gibi göstermek
  // olurdu. Böyle durumda diğer kartların "veri yok" hali gibi durulur.
  function maintenanceKnown(mt) {
    if (!isPlainObject(mt)) return false;
    return ["disk", "stale_processes", "stale_git"].some(function (key) {
      return isPlainObject(mt[key]);
    });
  }

  // Alt bölüm hataları — notifier.py::_maintenance_sentences'in gezdiği
  // üç anahtarın aynısı; rozet metni de oradaki "okunamadı" dilini kullanıyor.
  function maintenanceErrors(mt) {
    if (!isPlainObject(mt)) return [];
    var errors = [];
    ["disk", "stale_processes", "stale_git"].forEach(function (key) {
      var section = maintenanceSection(mt, key);
      if (section && section.error) errors.push(String(section.error));
    });
    return errors;
  }

  // Kaç bulgu var: dolu disk + unutulmuş süreç + eski commitlenmemiş repo.
  function maintenanceFindings(mt) {
    if (!isPlainObject(mt)) return 0;
    var disk = maintenanceSection(mt, "disk");
    return listOrEmpty(disk && disk.full).filter(isPlainObject).length
      + maintenanceItems(mt, "stale_processes").length
      + maintenanceItems(mt, "stale_git").length;
  }

  // Eşikler istatistik kutusunun alt satırına sığacak kadar kısa: ham
  // "disk_threshold_percent" değil, kullanıcının panelde gördüğü ölçü
  // (doluluk %, süre sa, gün).
  function fmtMaintenanceThresholds(mt) {
    var parts = [];
    var disk = maintenanceSection(mt, "disk");
    var procs = maintenanceSection(mt, "stale_processes");
    var git = maintenanceSection(mt, "stale_git");
    if (disk && typeof disk.threshold_percent === "number") parts.push("disk >%" + disk.threshold_percent);
    if (procs && typeof procs.min_hours === "number") parts.push("süreç >" + procs.min_hours + "sa");
    if (git && typeof git.min_days === "number") parts.push("git >" + git.min_days + "gün");
    return parts.join(" · ");
  }

  // Satır listesi boşken hücreye yazılacak metin. Alt bölüm hata yüzünden
  // boşsa "bulgu yok" demek yanlış olur — kısa bir hata notu basılır,
  // liste sessizce kaybolmaz.
  function maintenanceEmptyText(section, okText) {
    if (section && section.error) return "okunamadı: " + String(section.error);
    return okText;
  }

  function renderMaintenance(mt) {
    mt = isPlainObject(mt) ? mt : {};
    if (!maintenanceKnown(mt)) {
      setBadge(qs("maintenance-badge"), "neutral", "veri yok");
      setText("maintenance-error", "maintenance bölümü okunamadı");
      renderTable("maintenance-disk-body", 3, [], "veri yok");
      renderTable("maintenance-process-body", 3, [], "veri yok");
      renderTable("maintenance-git-body", 3, [], "veri yok");
      return;
    }

    var errors = maintenanceErrors(mt);
    var findings = maintenanceFindings(mt);
    setBadge(
      qs("maintenance-badge"),
      errors.length ? "bad" : findings > 0 ? "warn" : "ok",
      errors.length ? "kısmen okunamadı" : findings > 0 ? "bulgu var" : "her şey yolunda"
    );
    setText("maintenance-error", errors.join(" · "));

    var disk = maintenanceSection(mt, "disk");
    var diskFull = listOrEmpty(disk && disk.full).filter(isPlainObject);
    renderTable(
      "maintenance-disk-body",
      3,
      diskFull.map(function (d) {
        return [
          String(d.path || "—"),
          typeof d.percent === "number" ? "%" + d.percent : "—",
          typeof d.free_gb === "number" ? num(d.free_gb) + " GB" : "—"
        ];
      }),
      disk && typeof disk.threshold_percent === "number"
        ? "disk %" + disk.threshold_percent + " eşiğinin altında"
        : "eşik verisi yok"
    );

    var procs = maintenanceSection(mt, "stale_processes");
    var procItems = maintenanceItems(mt, "stale_processes");
    renderTable(
      "maintenance-process-body",
      3,
      procItems.map(function (p) {
        return [
          String(p.name || "—"),
          p.pid != null ? String(p.pid) : "—",
          typeof p.hours === "number" ? num(p.hours) + " sa" : "—"
        ];
      }),
      maintenanceEmptyText(procs, "unutulmuş süreç yok")
    );

    var git = maintenanceSection(mt, "stale_git");
    var gitItems = maintenanceItems(mt, "stale_git");
    renderTable(
      "maintenance-git-body",
      3,
      gitItems.map(function (g) {
        return [
          String(g.name || "—"),
          num(g.dirty_count),
          typeof g.age_days === "number" ? num(g.age_days) + " gün" : "—"
        ];
      }),
      maintenanceEmptyText(git, "eski commitlenmemiş repo yok")
    );
  }

  function renderOverallStatus(data) {
    var git = Array.isArray(data.git) ? data.git : [];
    var cor = data.cor || {};
    var bs = data.borsasite || {};
    var rb = data.readbunny || {};
    var vault = data.vault || {};
    var mt = data.maintenance || {};

    var anyRepoError = git.some(function (r) { return !!r.error; });
    var anyDirty = git.some(function (r) { return (r.dirty_count || 0) > 0; });
    // Bakım bulgusu tek başına topbar'ı "dikkat"e çeker; bakım bölümünün
    // tamamı okunamıyorsa ise "sorun var"dır (sessizce yeşil kalmaz).
    var maintenanceFailed = !maintenanceKnown(mt) || maintenanceErrors(mt).length > 0;
    var maintenanceFindingsCount = maintenanceFindings(mt);

    // durum --json kaynakları: notifier'ın DAR koşullarıyla AYNI ciddiyet.
    // Panel "dikkat" derken Telegram susuyorsa (ya da tersi) iki yüz birbirini
    // düzeltmiyormuş gibi görünür — o yüzden eşikler burada da birebir aynı:
    // erişilememe = sorun; veri/indeks bayatı ve onay bekleyen = dikkat. Sürekli >0 olan `bayat_readme`/`kırık link`
    // sayıları kasıtlı olarak topbar'ı etkilemez (spam olmasın diye).
    var atlas = isPlainObject(data.atlas) ? data.atlas : {};
    var ork = isPlainObject(data.orkestra) ? data.orkestra : {};
    var harita = isPlainObject(data.harita) ? data.harita : {};
    var durumBroken =
      atlas.reachable !== true || ork.reachable !== true || harita.reachable !== true;
    var durumBad =
      durumBroken;
    var durumWarn = (ork.onay_bekleyen || 0) > 0 || harita.indeks_bayat === true
      || atlas.veri_bayat === true;

    var level = "ok";
    if (anyRepoError || !cor.reachable || !bs.reachable || !rb.reachable || vault.error || maintenanceFailed || durumBad) {
      level = "bad";
    } else if (anyDirty || (rb.error_count || 0) > 0 || maintenanceFindingsCount > 0 || durumWarn) {
      level = "warn";
    }

    var dot = qs("status-dot");
    var text = qs("status-text");
    if (dot) {
      dot.classList.remove("ok", "warn", "bad");
      dot.classList.add(level);
    }
    if (text) {
      text.textContent = level === "ok" ? "her şey normal" : level === "warn" ? "dikkat gerektiren nokta var" : "sorun var";
    }
  }

  function render(data) {
    renderStats(data);
    renderRepoTable(data.git);
    renderCor(data.cor);
    renderBorsasite(data.borsasite);
    renderReadbunny(data.readbunny);
    renderVault(data.vault);
    renderAtlas(data.atlas);
    renderOrkestra(data.orkestra);
    renderHarita(data.harita);
    renderMaintenance(data.maintenance);
    renderOverallStatus(data);
    var updatedEl = qs("updated-at");
    if (updatedEl) {
      if (data.collected_at) {
        updatedEl.textContent = "güncellendi: " + fmtDate(new Date(data.collected_at * 1000).toISOString());
      } else {
        updatedEl.textContent = "";
      }
    }
  }

  function setConnLost(lost) {
    var badge = qs("conn-badge");
    if (!badge) return;
    if (lost) badge.classList.add("visible");
    else badge.classList.remove("visible");
  }

  function fetchSummary() {
    fetch("/api/summary", { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        setConnLost(false);
        render(data);
      })
      .catch(function () {
        // son bilinen veri ekranda kalır, sadece rozet görünür olur
        setConnLost(true);
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    fetchSummary();
    setInterval(fetchSummary, REFRESH_MS);
  });
})();
"""
