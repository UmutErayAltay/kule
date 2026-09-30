"""app/main.py testleri: FastAPI TestClient ile `/` ve `/api/summary`.

Config yükleme modül import zamanında bir kez yapıldığı için (`app.main`
modül seviyesinde `_config`/`_config_error` set eder), burada gerçek
`load_config`'i tekrar tetiklemek yerine doğrudan bu modül-seviyesi
değişkenleri monkeypatch ediyoruz — böylece hem "config var" hem "config
yok" senaryoları gerçek dosya sistemine dokunmadan test edilebiliyor.
`get_cached_summary` de gerçek collector'ları tetiklemesin diye sahte bir
fonksiyonla değiştiriliyor.
"""
from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.web import page as page_module
from app.web import script as script_module
from app.web import styles as styles_module


@pytest.fixture
def client():
    return TestClient(main_module.app)


def test_api_summary_with_valid_config_returns_summary(monkeypatch, client):
    monkeypatch.setattr(main_module, "_config", {"repo_roots": []})
    monkeypatch.setattr(main_module, "_config_error", None)
    fake_summary = {"git": [], "cor": {"reachable": True}, "collected_at": 123.0}
    monkeypatch.setattr(main_module, "get_cached_summary", lambda config: fake_summary)

    resp = client.get("/api/summary")

    assert resp.status_code == 200
    assert resp.json() == fake_summary


def test_api_summary_with_missing_config_returns_500(monkeypatch, client):
    monkeypatch.setattr(main_module, "_config", None)
    monkeypatch.setattr(main_module, "_config_error", "config.yaml bulunamadı")

    resp = client.get("/api/summary")

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == main_module.CONFIG_HELP
    assert body["detail"] == "config.yaml bulunamadı"


def test_api_summary_passes_config_to_get_cached_summary(monkeypatch, client):
    sentinel_config = {"marker": "sentinel"}
    monkeypatch.setattr(main_module, "_config", sentinel_config)
    monkeypatch.setattr(main_module, "_config_error", None)

    received = {}

    def fake_get_cached_summary(config):
        received["config"] = config
        return {"ok": True}

    monkeypatch.setattr(main_module, "get_cached_summary", fake_get_cached_summary)

    client.get("/api/summary")

    assert received["config"] is sentinel_config


def test_dashboard_with_missing_config_returns_500_html(monkeypatch, client):
    monkeypatch.setattr(main_module, "_config", None)
    monkeypatch.setattr(main_module, "_config_error", "config.yaml bulunamadı")

    resp = client.get("/")

    assert resp.status_code == 500
    assert "text/html" in resp.headers["content-type"]
    assert main_module.CONFIG_HELP in resp.text


def test_dashboard_with_valid_config_renders_page(monkeypatch, client):
    monkeypatch.setattr(main_module, "_config", {})
    monkeypatch.setattr(main_module, "_config_error", None)

    resp = client.get("/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Kontrol Kulesi" in resp.text


def test_dashboard_page_import_error_returns_explanatory_500(monkeypatch, client):
    monkeypatch.setattr(main_module, "_config", {})
    monkeypatch.setattr(main_module, "_config_error", None)

    # app.web.page zaten import edilmiş olabilir; sys.modules'da None koymak
    # "from app.web.page import ..." ifadesinin ImportError fırlatmasını sağlar
    # (Python import sisteminin belgelenmiş davranışı).
    monkeypatch.setitem(sys.modules, "app.web.page", None)

    resp = client.get("/")

    assert resp.status_code == 500
    assert "app/web/page.py henüz hazır değil" in resp.text


def test_dashboard_import_error_does_not_affect_api_summary(monkeypatch, client):
    """ImportError yalnızca `/`'ı etkilemeli, `/api/summary`'yi etkilememeli
    (main.py'nin lazy-import izolasyon niyeti)."""
    monkeypatch.setattr(main_module, "_config", {"repo_roots": []})
    monkeypatch.setattr(main_module, "_config_error", None)
    monkeypatch.setattr(main_module, "get_cached_summary", lambda config: {"ok": True})
    monkeypatch.setitem(sys.modules, "app.web.page", None)

    resp = client.get("/api/summary")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_app_has_expected_title():
    assert main_module.app.title == "kule"


# --- dalga F: bakım kartı -------------------------------------------------
#
# Bu testler HTML/JS'in kendisini doğrulayamaz (tarayıcı yok) ama panelin
# İKİNCİ sözleşmesini korur: `page.py`'daki her DOM id, `script.py`'da
# karşılık gelen `qs(...)`/`setText(...)` çağrısına sahip olmalı. Tek yönlü
# bir eşleşme kırılırsa (id var ama JS dokunmuyor) sayfa sessizce boş
# kalır — CLAUDE.md'deki "DOM id senkronizasyonu" kuralı tam olarak bunu
# anlatır. Aşağıdaki eşleşme listesi ikisini birlikte kontrol eder.

MAINTENANCE_DOM_IDS = [
    "maintenance-badge",
    "maintenance-error",
    "maintenance-disk-body",
    "maintenance-process-body",
    "maintenance-git-body",
]

# Bu id'ler JS tarafından DİĞER fonksiyonlarda zaten kullanılıyordu; liste
# elle tutuluyor ama eşleşme kontrolü "her id JS'te en az bir kez geçiyor"
# kuralını tümüne uygular.
EXPECTED_DOM_IDS = MAINTENANCE_DOM_IDS + [
    "status-dot",
    "status-text",
    "conn-badge",
    "updated-at",
    "repo-table-body",
    "cor-badge",
    "cor-error",
    "cor-health-kv",
    "cor-metrics-kv",
    "borsasite-badge",
    "borsasite-error",
    "borsasite-health-kv",
    "borsasite-last-trade",
    "borsasite-last-pred",
    "readbunny-badge",
    "readbunny-error",
    "readbunny-total",
    "readbunny-pending",
    "readbunny-errors",
    "readbunny-updated",
    "vault-badge",
    "vault-error",
    "vault-broken",
    "vault-orphan",
    "vault-open-threads",
    "vault-total-threads",
]

# İstatistik kutuları `setStatTile("<prefix>", ...)` ile güncelleniyor; JS'te
# tam id değil ÖN EK görünür (setStatTile içde `prefix + "-tile"` üretiyor).
# Bu yüzden o id'ler prefix üzerinden doğrulanır.
STAT_TILE_PREFIXES = [
    "stat-dirty",
    "stat-cor",
    "stat-borsasite",
    "stat-readbunny",
    "stat-vault",
    "stat-maintenance",
]


@pytest.fixture
def dashboard_html(client, monkeypatch):
    monkeypatch.setattr(main_module, "_config", {})
    monkeypatch.setattr(main_module, "_config_error", None)
    resp = client.get("/")
    assert resp.status_code == 200
    return resp.text


def test_dashboard_renders_maintenance_stat_tile(dashboard_html):
    assert 'id="stat-maintenance-tile"' in dashboard_html
    assert 'id="stat-maintenance-value"' in dashboard_html
    assert 'id="stat-maintenance-sub"' in dashboard_html
    # kutu başlığı da bakımı Türkçe olarak adlandırıyor olmalı
    assert "bakım bulgusu" in dashboard_html


def test_dashboard_renders_maintenance_card_tables(dashboard_html):
    assert 'id="maintenance-badge"' in dashboard_html
    assert 'id="maintenance-error"' in dashboard_html
    assert 'id="maintenance-disk-body"' in dashboard_html
    assert 'id="maintenance-process-body"' in dashboard_html
    assert 'id="maintenance-git-body"' in dashboard_html
    # üç alt bölümün başlıkları da görünür olmalı
    assert "dolu disk" in dashboard_html
    assert "unutulmuş süreç" in dashboard_html
    assert "eski commitlenmemiş repo" in dashboard_html


def test_dashboard_maintenance_bodies_start_with_loading_row(dashboard_html):
    """JS ilk render'dan önce bu gövdeler "yükleniyor" satırı taşımalı —
    aksi halde ilk boyama boş bir tablo görünür."""
    for body_id in ("maintenance-disk-body", "maintenance-process-body", "maintenance-git-body"):
        marker = f'<tbody id="{body_id}">'
        start = dashboard_html.index(marker)
        section = dashboard_html[start : start + 160]
        assert "yükleniyor" in section
        assert 'colspan="3"' in section


@pytest.mark.parametrize("dom_id", EXPECTED_DOM_IDS)
def test_every_dom_id_is_wired_in_script(dashboard_html, dom_id):
    """page.py'deki her id, script.py'da karşılık gelen bir çağrıda
    geçmelidir — aksi halde `qs()` null döner, sayfa sessizce boş kalır."""
    assert f'id="{dom_id}"' in dashboard_html, f"{dom_id} HTML'de yok"
    assert dom_id in script_module.DASHBOARD_JS, f"{dom_id} JS'te kullanılmıyor"


@pytest.mark.parametrize("prefix", STAT_TILE_PREFIXES)
def test_every_stat_tile_is_wired_in_script(dashboard_html, prefix):
    """İstatistik kutusu HTML'de üç id (tile/value/sub) taşır, JS'te ise
    `setStatTile("<prefix>"` çağrısı geçer — ikisi birlikte doğrulanır."""
    for suffix in ("-tile", "-value", "-sub"):
        assert f'id="{prefix}{suffix}"' in dashboard_html, f"{prefix}{suffix} HTML'de yok"
    assert f'"{prefix}"' in script_module.DASHBOARD_JS, f"{prefix} JS'te kullanılmıyor"


def test_maintenance_card_is_reachable_from_render_entrypoint():
    """Asıl senkronizasyon kuralı: HTML'deki id'ye JS'ten ulaşılabilir
    OLMALI. `renderMaintenance` tanımlı olmak yetmez — `render()` onu
    çağırmalı, yoksa kart hiç güncellenmez ve sayfa sessizce "yükleniyor…"
    satırında kalır. Aşağıdaki eşleşme kontrolü id'nin JS'te GEÇMESİNİ
    yakalar; bu test ise çağrının `render()` gövdesinde olduğunu doğrular.
    """
    js = script_module.DASHBOARD_JS
    render_start = js.index("function render(data) {")
    render_body = js[render_start : js.index("function setConnLost", render_start)]
    assert "renderMaintenance(data.maintenance);" in render_body
    # rozet ve hata satırı doğrudan qs()/setText() ile, tablo gövdeleri ise
    # renderTable(<id>, ...) ilk argümanı olarak okunur
    assert 'qs("maintenance-badge")' in js
    assert 'setText("maintenance-error"' in js
    for body_id in ("maintenance-disk-body", "maintenance-process-body", "maintenance-git-body"):
        assert f'renderTable(\n      "{body_id}"' in js, f"{body_id} renderTable'a verilmiyor"


def test_maintenance_card_is_present_alongside_existing_cards(dashboard_html):
    """Bakım kartı EKLENDİ — mevcut kartların hiçbiri kaybolmamalı."""
    for heading in (
        "cor (claude-openrouter)",
        "BorsaSite",
        "readbunny",
        "vault (Mt3Ui55OS)",
        "bakım (raporlar, müdahale etmez)",
    ):
        assert heading in dashboard_html


def test_maintenance_json_shape_is_documented_in_page_docstring(dashboard_html):
    """page.py docstring'i `/api/summary` şeklini belgeliyor; bakım
    alanı orada da geçmelidir (yeni alan eklendiğinde güncellenmesi gereken
    yerlerden biri)."""
    assert '"maintenance"' in page_module.__doc__


def test_stylesheet_has_sixth_stat_tile_capacity(dashboard_html):
    """Altıncı istatistik kutusu için ızgara güncellendi: altı kutu tek
    sıraya sığmayacağı için 3 kolona geçildi. `repeat(5, ...)` kalmamalı."""
    assert "repeat(5, 1fr)" not in styles_module.DASHBOARD_CSS
    assert "repeat(3, 1fr)" in styles_module.DASHBOARD_CSS
    # bakım kartı tam genişlikte bir satır alıyor
    assert ".card-wide" in styles_module.DASHBOARD_CSS


# --- dalga G: atlas / orkestra / harita kartları -------------------------
#
# Aynı sözleşme, yeni üç kaynak için: `page.py`'daki her DOM id
# `script.py`'da karşılık gelen bir `qs()`/`setText()`/`setStatTile()`
# çağrısına sahip olmalı ve tersi de doğru (JS'te olan ama HTML'de olmayan
# id de sessizce hiçbir işe yaramaz).

DURUM_DOM_IDS = [
    "atlas-badge",
    "atlas-error",
    "atlas-bulgu-onem-kv",
    "atlas-repo-sayisi",
    "atlas-kirli-repo",
    "atlas-push-bekleyen",
    "atlas-push-bilinmeyen",
    "atlas-bayat-readme",
    "atlas-bulgu-toplam",
    "atlas-todo-toplam",
    "atlas-son-tarama",
    "orkestra-badge",
    "orkestra-error",
    "orkestra-gorev-durum-kv",
    "orkestra-kota-kv",
    "orkestra-gorev-toplam",
    "orkestra-onay-bekleyen",
    "orkestra-basarisiz",
    "orkestra-kanitsiz",
    "harita-badge",
    "harita-error",
    "harita-not-sayisi",
    "harita-kirik-link",
    "harita-yetim-not",
    "harita-tutarlilik-uyari",
    "harita-son-indeks",
]

DURUM_STAT_TILE_PREFIXES = ["stat-atlas", "stat-orkestra", "stat-harita"]


@pytest.mark.parametrize("dom_id", DURUM_DOM_IDS)
def test_every_durum_dom_id_is_wired_in_script(dashboard_html, dom_id):
    """Yeni kartların her id'si HTML'de var VE JS'te kullanılıyor."""
    assert f'id="{dom_id}"' in dashboard_html, f"{dom_id} HTML'de yok"
    assert dom_id in script_module.DASHBOARD_JS, f"{dom_id} JS'te kullanılmıyor"


@pytest.mark.parametrize("prefix", DURUM_STAT_TILE_PREFIXES)
def test_every_durum_stat_tile_is_wired_in_script(dashboard_html, prefix):
    """Yeni istatistik kutuları da tile/value/sub üçlüsüyle birlikte
    `setStatTile("<prefix>"` çağrısına bağlı."""
    for suffix in ("-tile", "-value", "-sub"):
        assert f'id="{prefix}{suffix}"' in dashboard_html, f"{prefix}{suffix} HTML'de yok"
    assert f'"{prefix}"' in script_module.DASHBOARD_JS, f"{prefix} JS'te kullanılmıyor"


def test_durum_cards_are_called_from_render_entrypoint():
    """Asıl senkronizasyon kuralı: kart id'leri HTML'de olmak yetmez,
    `render()` onlara ULAŞMALI — aksi halde kart "yükleniyor…" satırında
    kalır ve hiçbir hata fırlatmaz."""
    js = script_module.DASHBOARD_JS
    render_start = js.index("function render(data) {")
    render_body = js[render_start : js.index("function setConnLost", render_start)]
    for cagri in (
        "renderAtlas(data.atlas);",
        "renderOrkestra(data.orkestra);",
        "renderHarita(data.harita);",
    ):
        assert cagri in render_body, f"{cagri} render() gövdesinde yok"


def test_durum_cards_are_present_alongside_existing_cards(dashboard_html):
    """Yeni kartlar EKLENDİ — mevcut kartların hiçbiri kaybolmamalı."""
    for heading in (
        "cor (claude-openrouter)",
        "BorsaSite",
        "readbunny",
        "vault (Mt3Ui55OS)",
        "harita (vault)",
        "bakım (raporlar, müdahale etmez)",
    ):
        assert heading in dashboard_html
    # yeni kart etiketleri
    assert ">atlas<" in dashboard_html.replace(" ", "")
    assert ">orkestra<" in dashboard_html.replace(" ", "")


def test_durum_json_shape_is_documented_in_page_docstring():
    """page.py docstring'i `/api/summary` şeklini belgeliyor; üç yeni alan
    da orada geçmeli."""
    for anahtar in ('"atlas"', '"orkestra"', '"harita"'):
        assert anahtar in page_module.__doc__


def test_durum_stat_tiles_fit_the_grid(dashboard_html):
    """Dokuz kutu üç kolonlu ızgarada üç satır olur — CSS'te altı kutunun
    ızgarası kalmamalı, `.card-wide` yeni geniş kartlar için de var."""
    assert "repeat(3, 1fr)" in styles_module.DASHBOARD_CSS
    assert ".card-wide" in styles_module.DASHBOARD_CSS
    # dokuz stat-tile gerçekten var mı
    assert dashboard_html.count('class="stat-tile"') == 9


def test_unknown_counts_render_as_bilinmiyor_not_zero():
    """`null` sayılar panelde 0 DEĞİL "bilinmiyor" olarak görünür —
    sözleşmenin "bilinmeyen için null (0 DEĞİL)" kuralının panel karşılığı.
    JS'te `|| 0` gibi bir kısayol olsaydı sıfır basılırdı.

    Kontrol YALNIZCA yeni kaynakların gövdelerine uygulanır: eski
    git/vault kodunda zaten `|| 0` kullanımı var (o sayaçların `null`
    sözleşmesi farklıdır) ve bu dalga onları değiştirmiyor."""
    js = script_module.DASHBOARD_JS
    assert 'var UNKNOWN = "bilinmiyor";' in js
    assert "function count(value)" in js

    for baslangic, bitis in (
        ("function count(value)", "function setCountText"),
        ("function renderAtlas(", "function renderCor("),
    ):
        govde = js[js.index(baslangic) : js.index(bitis)]
        assert "|| 0" not in govde, f"{baslangic} gövdesinde `|| 0` kısayolu var"
        assert "?? 0" not in govde, f"{baslangic} gövdesinde `?? 0` kısayolu var"
