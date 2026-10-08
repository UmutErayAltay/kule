"""`app/launcher.py` ve `/api/tools*` uçlarının testleri.

Kural (CLAUDE.md "Test yazarken"): testler GERÇEK süreç başlatmaz,
gerçek port'a istek atmaz, `~/.kule/launched.json`'a dokunmaz. Fixtür
`launcher.STATE_FILE`'i `tmp_path`'e yönlendirir, `subprocess.Popen` ve
`httpx.get` sahte nesnelerle beslenir. Yani bu dosya asla kule'nin
gerçekten çalışan atlas/orkestra/harita panellerini etkilemez —
`launcher.shutdown` da import anında `atexit`'e kaydolmadığı için
test sonunda kendiliğinden süreç kapatmaya çalışmaz.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import launcher
from app import main as main_module

# atlas/orkestra/harita ayrı bir depodan kurulan araçlardır; yoksa onları GERÇEKTEN
# bulmayı bekleyen testler atlanır (CI'da kurulu değiller).
araclar_gerekli = pytest.mark.skipif(
    not all(shutil.which(ad) for ad in ("atlas", "orkestra", "harita")),
    reason="atlas/orkestra/harita kurulu değil",
)

TOOL_ADLARI = ["atlas", "orkestra", "harita", "liman"]


# ------------------------------------------------------------- fixtürler


@pytest.fixture(autouse=True)
def izole_state(tmp_path, monkeypatch):
    """Pid kayıt dosyasını tmp'ye al — gerçek `~/.kule`ye dokunma."""
    state_file = tmp_path / "kule" / "launched.json"
    monkeypatch.setattr(launcher, "STATE_FILE", state_file)
    # Modül düzeyindeki bellek içi durum testler arasında sızmasın.
    launcher._son_dokunus.clear()
    launcher._cerceveli.clear()
    launcher._kendi_pidlerimiz.clear()
    yield state_file
    launcher.stop_reaper()
    launcher._son_dokunus.clear()
    launcher._cerceveli.clear()
    launcher._kendi_pidlerimiz.clear()


@pytest.fixture
def config():
    """`harita` için vault veren, exe yolları GEÇERSİZ bir config.

    Exe'ler geçersiz yoldadır (`sahte/atlas.exe`) ki `Popen` sahte
    nesne döndüğünde gerçek dosya aranmaya çalışmasın.
    """
    return {
        "vault": {"path": "C:/vault/ornek"},
        "atlas": {"komut": ["C:/sahte/atlas.exe"]},
        "orkestra": {"komut": ["C:/sahte/orkestra.exe"]},
    }


@pytest.fixture
def baslatildi(monkeypatch):
    """`Popen`'u sahte süreçlerle besler; çağrıları kaydeder.

    Sahte süreç kendi pid'ini bilir ve `cmdline()`'si araç adını içerir,
    böylece `_pid_alive` "çalışıyor" diye doğru karar verir.
    """
    calls: list[list[str]] = []
    next_pid = [4100]

    class FakeProc:
        def __init__(self, argv, pid):
            self.argv = argv
            self.pid = pid
            self.terminated = False
            self.killed = False

    def fake_popen(argv, **kwargs):
        calls.append(list(argv))
        next_pid[0] += 1
        return FakeProc(argv, next_pid[0])

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


@pytest.fixture
def canli_surecler(monkeypatch):
    """Sahte süreçleri pid -> (ad, canlı) tablosuyla izler."""
    processes: dict[int, tuple[str, bool]] = {}
    calls: list[list[str]] = []
    next_pid = [4200]

    class FakeProc:
        def __init__(self, pid, ad):
            self.pid = pid
            self.ad = ad

    def fake_popen(argv, **kwargs):
        calls.append(list(argv))
        next_pid[0] += 1
        pid = next_pid[0]
        # argv[0] exe, sonra alt komut; araç adı exe yolunun içinde.
        ad = next(t for t in TOOL_ADLARI if t in str(argv[0]))
        processes[pid] = (ad, True)
        return FakeProc(pid, ad)

    def fake_pid_exists(pid):
        return pid in processes

    def fake_process(pid):
        ad, alive = processes[pid]

        class P:
            def cmdline(self):
                if not alive:
                    raise launcher.psutil.NoSuchProcess(pid)
                return [f"C:\\{ad}.exe", "web"]

            def terminate(self_inner):
                processes[pid] = (ad, False)

            def kill(self_inner):
                processes[pid] = (ad, False)

        P.pid = pid
        return P()

    def fake_wait_procs(procs, timeout=None):
        alive = [p for p in procs if processes.get(p.pid, (None, False))[1]]
        return [], alive

    import psutil

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(psutil, "pid_exists", fake_pid_exists)
    monkeypatch.setattr(psutil, "Process", fake_process)
    monkeypatch.setattr(psutil, "wait_procs", fake_wait_procs)
    return processes, calls


# ------------------------------------------------------------ komut çözümü


def test_tool_command_kullanilan_config_exe_yolunu_verir(config):
    argv = launcher.tool_command("atlas", config)

    assert argv == ["C:/sahte/atlas.exe", "web", "--port", "8770"]


def test_tool_command_portlar_gorev_karariyla_sabit():
    # Portlar config'ten GELMEZ: görev kararı sabit (8770/8780/8900/8795).
    assert launcher.TOOLS["atlas"]["port"] == 8770
    assert launcher.TOOLS["orkestra"]["port"] == 8780
    assert launcher.TOOLS["harita"]["port"] == 8900
    assert launcher.TOOLS["liman"]["port"] == 8795


@araclar_gerekli
def test_tool_command_harita_vault_pozisyonel_alir(config):
    argv = launcher.tool_command("harita", config)

    assert "C:/vault/ornek" in argv
    assert argv[-2:] == ["--port", "8900"]


@araclar_gerekli
def test_tool_command_vault_yoksa_harita_yine_calisir():
    argv = launcher.tool_command("harita", {})

    assert argv is not None
    assert "C:/vault/ornek" not in argv
    assert argv[-2:] == ["--port", "8900"]


def test_tool_command_bilinmeyen_arac_none_doner(config):
    assert launcher.tool_command("olmayan", config) is None


def test_tool_command_exe_bulunamazsa_none_doner(monkeypatch):
    """Config'te komut yok, PATH'te yok, Scripts dizini yok -> None.

    `_DETACH_FLAGS` sıfırlanınca `.exe` son eki de düşer, yani arama
    `atlas` (çıplak ad) ve `posix_user` şemasında yapılır — ikisi de
    bulunamayacak şekilde sabitleniyor.
    """
    monkeypatch.setattr(launcher.shutil, "which", lambda ad: None)
    monkeypatch.setattr(launcher, "_DETACH_FLAGS", 0)
    monkeypatch.setattr(launcher.sysconfig, "get_path", lambda key, scheme: "/yok/boyle")

    assert launcher.tool_command("atlas", {}) is None


# -------------------------------------------------------------- durum ucu


def test_list_status_uc_araci_eksiksiz_doner(config):
    araclar = launcher.list_status(config)

    assert [t["ad"] for t in araclar] == TOOL_ADLARI


@araclar_gerekli
def test_status_alanlari_dogru_konumda(config):
    for arac in launcher.list_status(config):
        assert arac["port"] in (8770, 8780, 8900, 8795)
        assert arac["url"] == f"http://127.0.0.1:{arac['port']}/"
        assert arac["exe"] is not None
        assert arac["komut"][0] == arac["exe"]
        # Hiçbiri başlatılmadı -> kapalı, pid yok.
        assert arac["calisiyor"] is False
        assert arac["pid"] is None


def test_hazir_tanimli_degilse_null_doner(config):
    # Ölçemedim -> null, False değil ("veritabanı yok" iddiası uydurmayız).
    for arac in launcher.list_status(config):
        assert arac["hazir"] is None


def test_hazir_dosya_tanimliysa_dosya_varligina_bakar(tmp_path, monkeypatch):
    db = tmp_path / "atlas.db"
    db.write_text("x", encoding="utf-8")
    cfg = {"atlas": {"komut": ["C:/sahte/atlas.exe"], "hazirlik_dosyasi": str(db)}}

    assert launcher.status_of("atlas", cfg)["hazir"] is True

    db.unlink()
    assert launcher.status_of("atlas", cfg)["hazir"] is False


# ------------------------------------------------- pid canlilik kontrolu


def test_olu_pid_calisiyor_gosterilmez(config, izole_state):
    """KAYITTA pid var ama süreç ölmüşse panel "kapalı" demeli.

    Bu, kule yeniden başladığında en sık görülen durum: pid dosyası
    kaldı, süreç kalmadı. Yanlışlıkla "çalışıyor" denirse hem `/stop`
    çalışmaz hem de kullanıcı paneli açık sanır.
    """
    izole_state.parent.mkdir(parents=True, exist_ok=True)
    izole_state.write_text(json.dumps({"atlas": 999999}), encoding="utf-8")

    durum = launcher.status_of("atlas", config)

    assert durum["calisiyor"] is False
    assert durum["pid"] is None


def test_olu_pid_kayittan_silinir(config, izole_state):
    izole_state.parent.mkdir(parents=True, exist_ok=True)
    izole_state.write_text(json.dumps({"atlas": 999999}), encoding="utf-8")

    launcher.status_of("atlas", config)

    assert json.loads(izole_state.read_text(encoding="utf-8")) == {}


def test_yasayan_pid_sahiplenir_ve_korunur(config, canli_surecler, izole_state):
    processes, _ = canli_surecler
    durum, hata = launcher.start("atlas", config)
    assert hata is None
    pid = durum["pid"]
    assert processes[pid][1] is True

    # Kule yeniden "başlatıldı": aynı pid hâlâ kayıtta ve canlı.
    tekrar = launcher.status_of("atlas", config)

    assert tekrar["calisiyor"] is True
    assert tekrar["pid"] == pid


def test_pid_yeniden_kullanilmis_yabancisidir(config, canli_surecler, monkeypatch):
    """Pid canlı ama komut satırı BAŞKA bir program: sahiplenilmez.

    Windows pid'leri ölü süreçten sonra yeniden veriliyor; yalnız pid'e
    bakmak kule'nin rastgele bir süreci kapatmasına yol açardı.
    """
    import psutil

    class P:
        def cmdline(self):
            return ["C:\\Windows\\System32\\notepad.exe"]

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", lambda pid: P())

    assert launcher._pid_alive(1234, "atlas") is False


# ------------------------------------------------------------ idempotansi


def test_start_zaten_acikken_yeni_surec_acmaz(config, canli_surecler, izole_state):
    processes, calls = canli_surecler

    ilk, hata1 = launcher.start("atlas", config)
    ikinci, hata2 = launcher.start("atlas", config)

    assert hata1 is None and hata2 is None
    assert len(calls) == 1  # tek Popen
    assert ilk["pid"] == ikinci["pid"]
    assert ikinci["calisiyor"] is True


def test_stop_kapaliyken_hata_vermez(config, izole_state):
    durum, hata = launcher.stop("atlas", config)

    assert hata is None
    assert durum["calisiyor"] is False


def test_stop_calisan_sureci_kapatir_ve_kaydi_siler(config, canli_surecler, izole_state):
    processes, _ = canli_surecler
    baslatildi, _ = launcher.start("atlas", config)
    pid = baslatildi["pid"]

    durum, hata = launcher.stop("atlas", config)

    assert hata is None
    assert processes[pid][1] is False  # süreç öldürüldü
    assert durum["calisiyor"] is False
    assert json.loads(izole_state.read_text(encoding="utf-8")) == {}


def test_stop_idempotent_iki_kere_calisir(config, canli_surecler, izole_state):
    launcher.start("atlas", config)

    _, hata1 = launcher.stop("atlas", config)
    _, hata2 = launcher.stop("atlas", config)

    assert hata1 is None and hata2 is None


def test_restart_once_durdurur_sonra_baslatir(config, canli_surecler, izole_state):
    processes, calls = canli_surecler
    ilk, _ = launcher.start("atlas", config)
    ilk_pid = ilk["pid"]

    durum, hata = launcher.restart("atlas", config)

    assert hata is None
    assert processes[ilk_pid][1] is False  # eskisi kapandı
    assert durum["pid"] != ilk_pid  # yenisi açıldı
    assert len(calls) == 2


def test_bilinmeyen_arac_uc_kadar_hata_dondurur(config):
    assert launcher.start("olmayan", config)[1] == launcher.ERR_AD_YOK
    assert launcher.stop("olmayan", config)[1] == launcher.ERR_AD_YOK
    assert launcher.restart("olmayan", config)[1] == launcher.ERR_AD_YOK


def test_baslatma_hatasinda_alt_surec_metni_sizmaz(config, monkeypatch, izole_state):
    """Popen patlarsa istisna metni panele GİRMEZ — sabit cümle döner."""
    import psutil

    class P:
        def cmdline(self):
            return ["C:\\atlas.exe"]

    def patlar(*a, **kw):
        raise OSError(r"C:\Kullanici\secret\atlas.exe erişilemedi")

    monkeypatch.setattr(subprocess, "Popen", patlar)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(psutil, "Process", lambda pid: P())

    durum, hata = launcher.start("atlas", config)

    assert durum is None
    assert hata is not None
    assert "Kullanici" not in hata
    assert "secret" not in hata


def test_kendi_baslattigi_pid_komut_satirinda_ad_yoksa_bile_durur(config, monkeypatch, izole_state):
    """SÜREÇ SIZINTISI REGRESYON TESTİ.

    Kule `Popen` ile açtığı pid'i kaydeder, ama exe yolunda araç adı
    geçmeyebilir (örn. `komut: ["C:/bin/a.exe"]` ya da `python -c ...`).
    Komut satırı kontrolü burada yanlış "ölü" derse `/stop` pid'i
    bulamaz ve süreç ÖKSÜZ kalır — port dolu, panel kapatılamaz.
    `Popen`'dan gelen pid için yalnızca "yaşıyor mu" bakılmalı.
    """
    import psutil

    canli = {7777: True}

    class P:
        pid = 7777

        def cmdline(self):
            # Araç adı YOK: kule kendi yolundan başlatmış olabilir.
            return ["C:\\bin\\a.exe", "web"]

        def terminate(self):
            canli[7777] = False

        def kill(self):
            canli[7777] = False

    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: P())
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: canli.get(pid, False))
    monkeypatch.setattr(psutil, "Process", lambda pid: P())
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: ([], []))
    monkeypatch.setattr(launcher, "_kendi_pidlerimiz", set())

    durum, hata = launcher.start("atlas", config)
    assert hata is None
    assert durum["calisiyor"] is True
    assert durum["pid"] == 7777

    durum, hata = launcher.stop("atlas", config)

    assert hata is None
    assert canli[7777] is False  # GERÇEKTEN kapatıldı, sızıntı yok


# ------------------------------------------------------------------ health


def test_health_kapali_portta_erisilemez(monkeypatch):
    monkeypatch.setattr(launcher.httpx, "get", _hata_uret)

    sonuc = launcher.health("atlas")

    assert sonuc["erisilebilir"] is False
    assert sonuc["http_status"] is None
    assert sonuc["port"] == 8770


def test_health_durum_kodunu_doner(monkeypatch):
    monkeypatch.setattr(launcher.httpx, "get", lambda url, timeout=None: _Cevap(200))

    sonuc = launcher.health("atlas")

    assert sonuc["erisilebilir"] is True
    assert sonuc["http_status"] == 200


class _Cevap:
    """`httpx.get` sahtesi — sadece kule'nin okuduğu `status_code`."""

    def __init__(self, status_code: int):
        self.status_code = status_code


def _hata_uret(url, timeout=None):
    raise launcher.httpx.ConnectError("kapalı")


# ------------------------------------------------------------- API uclari


@pytest.fixture
def client():
    return TestClient(main_module.app)


@pytest.fixture
def temiz_config(monkeypatch):
    monkeypatch.setattr(main_module, "_config_error", None)
    monkeypatch.setattr(main_module, "_config", {})


def test_api_tools_listesi_eksiksiz(client, temiz_config):
    resp = client.get("/api/tools")

    assert resp.status_code == 200
    araclar = resp.json()["tools"]
    assert [t["ad"] for t in araclar] == TOOL_ADLARI
    assert all("port" in t and "calisiyor" in t for t in araclar)


@araclar_gerekli
def test_api_tools_start_stop_uc_kodu(client, temiz_config, canli_surecler, izole_state):
    baslat = client.post("/api/tools/atlas/start")
    assert baslat.status_code == 200
    assert baslat.json()["calisiyor"] is True

    durdur = client.post("/api/tools/atlas/stop")
    assert durdur.status_code == 200
    assert durdur.json()["calisiyor"] is False

    yeniden = client.post("/api/tools/atlas/restart")
    assert yeniden.status_code == 200
    assert yeniden.json()["calisiyor"] is True


def test_api_tools_bilinmeyen_arac_404(client, temiz_config):
    resp = client.post("/api/tools/olmayan/start")

    assert resp.status_code == 404
    # Girdi panele yansıtılmaz, sabit liste döner.
    assert resp.json()["gecerli"] == sorted(TOOL_ADLARI)


def test_api_tools_health_ucu(client, temiz_config, monkeypatch):
    monkeypatch.setattr(launcher.httpx, "get", lambda url, timeout=None: _Cevap(200))

    resp = client.get("/api/tools/atlas/health")

    assert resp.status_code == 200
    assert resp.json()["http_status"] == 200


def test_api_tools_config_yokken_500_ve_config_help(client, monkeypatch):
    monkeypatch.setattr(main_module, "_config", None)
    monkeypatch.setattr(main_module, "_config_error", "config.yaml bulunamadı")

    for resp in (
        client.get("/api/tools"),
        client.post("/api/tools/atlas/start"),
        client.post("/api/tools/atlas/stop"),
        client.post("/api/tools/atlas/restart"),
        client.get("/api/tools/atlas/health"),
    ):
        assert resp.status_code == 500
        assert resp.json()["error"] == main_module.CONFIG_HELP


# ------------------------------------------------------------------ HTML


def test_html_araclar_bolumu_ve_uc_araç_adı(client, temiz_config):
    html = client.get("/").text

    assert "Araçlar" in html
    for ad in TOOL_ADLARI:
        assert ad in html
        # sekme + panel + iframe alanı (id düzeni: tool-<eylem>-<araç>)
        assert f'id="tab-{ad}"' in html
        assert f'id="panel-{ad}"' in html
        assert f'id="tool-frame-{ad}"' in html
        assert f'tool-stop-{ad}' in html
        assert f'tool-restart-{ad}' in html
        assert f'tool-open-{ad}' in html
        # Başlat düğmesi YOK: sekmeye tıklayınca araç kendiliğinden açılır
        assert f'tool-start-{ad}' not in html
    assert 'id="tab-kule"' in html and 'id="panel-kule"' in html


def test_html_sekme_script_dogru_uclari_cagirir(client, temiz_config):
    """Sekme akışı start -> health bekle -> iframe; 30 sn'de bir touch."""
    html = client.get("/").text
    assert '"/api/tools/" + encodeURIComponent(ad) + "/" + action' in html
    assert "/health" in html and "/touch" in html
    assert 'createElement("iframe")' in html
    assert "sandbox" in html


# ------------------------------------------------------------------ frame_origin


def test_start_frame_origin_gecerli_env_eklenir(config, canli_surecler, monkeypatch):
    """frame_origin kalıba uyuyorsa Popen env'ine KULE_FRAME_ORIGIN eklenir."""
    captured_env = {}

    def fake_popen(argv, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return canli_surecler[1][0]  # ilk çağrı için sahte süreç

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    launcher.start("atlas", config, frame_origin="http://127.0.0.1:8790")

    assert "KULE_FRAME_ORIGIN" in captured_env
    assert captured_env["KULE_FRAME_ORIGIN"] == "http://127.0.0.1:8790"
    # Telegram anahtarları yine çıkmalı
    assert not any(k.startswith("KULE_TELEGRAM_") for k in captured_env)


def test_start_frame_origin_gecersiz_env_eklenmez(config, canli_surecler, monkeypatch):
    """frame_origin kalıba UYMUYORSA env'e HİÇ eklenmez."""
    captured_env = {}

    def fake_popen(argv, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return canli_surecler[1][0]

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    # https -> reddedilir
    launcher.start("atlas", config, frame_origin="https://127.0.0.1:8790")
    assert "KULE_FRAME_ORIGIN" not in captured_env

    # farklı host -> reddedilir
    captured_env.clear()
    launcher.start("atlas", config, frame_origin="http://192.168.1.5:8790")
    assert "KULE_FRAME_ORIGIN" not in captured_env

    # port yok -> reddedilir
    captured_env.clear()
    launcher.start("atlas", config, frame_origin="http://127.0.0.1")
    assert "KULE_FRAME_ORIGIN" not in captured_env

    # yol içeren -> reddedilir
    captured_env.clear()
    launcher.start("atlas", config, frame_origin="http://127.0.0.1:8790/path")
    assert "KULE_FRAME_ORIGIN" not in captured_env

    # None -> eklenmez
    captured_env.clear()
    launcher.start("atlas", config, frame_origin=None)
    assert "KULE_FRAME_ORIGIN" not in captured_env


def test_start_parent_ortaminda_KULE_FRAME_ORIGIN_varsa_cocuga_gecmez(
    config, canli_surecler, monkeypatch
):
    """Parent ortamında KULE_FRAME_ORIGIN varsa o da çocuğa geçmemeli."""
    import os

    monkeypatch.setenv("KULE_FRAME_ORIGIN", "http://evil.com")
    captured_env = {}

    def fake_popen(argv, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return canli_surecler[1][0]

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    launcher.start("atlas", config, frame_origin=None)

    assert "KULE_FRAME_ORIGIN" not in captured_env


def test_status_cerceve_alani_dogru(config, canli_surecler):
    """status_of sonucunda 'cerceve' alanı doğru: origin alıp çalışıyorsa True."""
    # Henüz başlatılmamış -> cerceve False
    durum = launcher.status_of("atlas", config)
    assert durum["cerceve"] is False

    # frame_origin ile başlat -> cerceve True
    launcher.start("atlas", config, frame_origin="http://127.0.0.1:8790")
    durum = launcher.status_of("atlas", config)
    assert durum["cerceve"] is True

    # Stop -> cerceve False
    launcher.stop("atlas", config)
    durum = launcher.status_of("atlas", config)
    assert durum["cerceve"] is False


# ------------------------------------------------------------------ touch


def test_touch_kendi_baslattigi_arac_kaydi_gunceller(config, canli_surecler, monkeypatch):
    """touch() kendi başlattığı aracın son dokunuş zamanını yazar."""
    launcher.start("atlas", config)
    once = launcher._son_dokunus.get("atlas")
    assert once is not None

    time.sleep(0.05)
    launcher.touch("atlas")
    sonra = launcher._son_dokunus.get("atlas")
    assert sonra > once


def test_touch_baska_surec_veya_baslatilmamis_noop(config, canli_surecler):
    """Başka süreçten açılmış veya başlatılmamış araca touch no-op."""
    # Başlatılmamış
    launcher.touch("atlas")
    assert "atlas" not in launcher._son_dokunus

    # Başlat
    launcher.start("atlas", config)

    # _kendi_pidlerimiz'den çıkar -> Elle açılmış gibi
    pid = launcher._read_state().get("atlas")
    launcher._kendi_pidlerimiz.discard(pid)

    once = launcher._son_dokunus.get("atlas")
    launcher.touch("atlas")
    # Değişmemeli
    assert launcher._son_dokunus.get("atlas") == once


def test_touch_bilinmeyen_arac_noop(config):
    """Bilinmeyen araç adı sessizce no-op."""
    launcher.touch("olmayan")  # exception fırlatmamalı
    assert "olmayan" not in launcher._son_dokunus


def test_api_tool_touch_200(client, temiz_config, canli_surecler, izole_state):
    """POST /api/tools/{ad}/touch -> 200 + {'ok': true}."""
    # Önce başlat
    client.post("/api/tools/atlas/start")

    resp = client.post("/api/tools/atlas/touch")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_api_tool_touch_bilinmeyen_404(client, temiz_config):
    """Bilinmeyen araç -> 404."""
    resp = client.post("/api/tools/olmayan/touch")
    assert resp.status_code == 404
    assert resp.json()["gecerli"] == sorted(TOOL_ADLARI)


def test_api_tool_touch_config_yok_500(client, monkeypatch):
    """Config yok -> 500 + CONFIG_HELP."""
    monkeypatch.setattr(main_module, "_config", None)
    monkeypatch.setattr(main_module, "_config_error", "config.yaml bulunamadı")

    resp = client.post("/api/tools/atlas/touch")
    assert resp.status_code == 500
    assert resp.json()["error"] == main_module.CONFIG_HELP


# ------------------------------------------------------------------ reap_idle / idle_minutes / reaper


def test_reap_idle_sure_dolmadi_kapatmaz(config, canli_surecler):
    """Süre dolmadı -> kapatma."""
    launcher.start("atlas", config)
    kapatilan = launcher.reap_idle(config, idle_minutes=15, simdi=time.monotonic())
    assert kapatilan == []


def test_reap_idle_sure_doldu_kapatir(config, canli_surecler):
    """Süre doldu -> stop çağırır."""
    launcher.start("atlas", config)
    simdi = time.monotonic() + 16 * 60  # 16 dakika sonra
    kapatilan = launcher.reap_idle(config, idle_minutes=15, simdi=simdi)
    assert "atlas" in kapatilan


def test_reap_idle_idle_minutes_sifir_kapatmaz(config, canli_surecler):
    """idle_minutes=0 -> kapatma (kapalı)."""
    launcher.start("atlas", config)
    simdi = time.monotonic() + 100 * 60
    kapatilan = launcher.reap_idle(config, idle_minutes=0, simdi=simdi)
    assert kapatilan == []


def test_reap_idle_kendi_pidlerimiz_disinda_dokunmaz(config, canli_surecler, izole_state):
    """Elle/başka süreçten açılmış (kendi_pidlerimiz dışı) araca dokunma."""
    # Elle state'e pid yaz
    izole_state.parent.mkdir(parents=True, exist_ok=True)
    izole_state.write_text(json.dumps({"atlas": 9999}), encoding="utf-8")
    # _kendi_pidlerimiz boş
    launcher._kendi_pidlerimiz.clear()
    launcher._son_dokunus["atlas"] = time.monotonic() - 100 * 60

    simdi = time.monotonic()
    kapatilan = launcher.reap_idle(config, idle_minutes=15, simdi=simdi)
    assert kapatilan == []


def test_reap_idle_orkestra_mesgul_kapatmaz(config, canli_surecler, monkeypatch):
    """orkestra meşgul -> kapatma."""
    launcher.start("orkestra", config)

    def mesgul(_cfg):
        return True

    simdi = time.monotonic() + 16 * 60
    kapatilan = launcher.reap_idle(config, idle_minutes=15, simdi=simdi, orkestra_mesgul=mesgul)
    assert kapatilan == []


def test_reap_idle_orkestra_okunamadi_kapatmaz(config, canli_surecler):
    """orkestra okunamadı (hata) -> güvenli taraf, kapatma."""
    launcher.start("orkestra", config)

    def hata(_cfg):
        raise RuntimeError("okunamadı")

    simdi = time.monotonic() + 16 * 60
    kapatilan = launcher.reap_idle(config, idle_minutes=15, simdi=simdi, orkestra_mesgul=hata)
    assert kapatilan == []


def test_reap_idle_orkestra_bosta_kapatir(config, canli_surecler, monkeypatch):
    """orkestra boşta -> kapatır."""
    launcher.start("orkestra", config)

    def bosta(_cfg):
        return False

    simdi = time.monotonic() + 16 * 60
    kapatilan = launcher.reap_idle(config, idle_minutes=15, simdi=simdi, orkestra_mesgul=bosta)
    assert "orkestra" in kapatilan


def test_idle_minutes_varsayilanlar(monkeypatch):
    """idle_minutes config'ten okunur; bozuksa varsayılan 15."""
    # Config yok
    assert launcher.idle_minutes({}) == 15.0

    # araclar blok yok
    assert launcher.idle_minutes({"baska": 1}) == 15.0

    # bool -> reddedilir (bool int'in alt türü ama sayı DEĞİLDİR)
    assert launcher.idle_minutes({"araclar": {"bosta_kapat_dakika": True}}) == 15.0

    # negatif -> reddedilir
    assert launcher.idle_minutes({"araclar": {"bosta_kapat_dakika": -5}}) == 15.0

    # string -> reddedilir
    assert launcher.idle_minutes({"araclar": {"bosta_kapat_dakika": "15"}}) == 15.0

    # 0 -> 0 (kapalı)
    assert launcher.idle_minutes({"araclar": {"bosta_kapat_dakika": 0}}) == 0.0

    # geçerli sayı
    assert launcher.idle_minutes({"araclar": {"bosta_kapat_dakika": 10}}) == 10.0
    assert launcher.idle_minutes({"araclar": {"bosta_kapat_dakika": 7.5}}) == 7.5


def test_start_reaper_hata_atan_loadconfig_olmez(monkeypatch):
    """start_reaper hata atan load_config_fn ile thread'i öldürmez."""
    def patlayan():
        raise RuntimeError("config yüklenemedi")

    thread = launcher.start_reaper(patlayan, kontrol_saniye=0.01)
    time.sleep(0.05)  # en az bir tur dönsün
    # Thread hâlâ canlı olmalı
    assert thread.is_alive()
    launcher.stop_reaper()


def test_stop_reaper_threadi_durdurur(monkeypatch):
    """stop_reaper thread'ini temiz durdurur."""
    def config_fn():
        return {}

    thread = launcher.start_reaper(config_fn, kontrol_saniye=0.01)
    time.sleep(0.02)
    launcher.stop_reaper()
    assert not thread.is_alive()
    # Tekrar çağrılsa hata vermemeli
    launcher.stop_reaper()


# ------------------------------------------------- çerçeve origin: sınırlar


@pytest.fixture
def env_yakala(canli_surecler, monkeypatch):
    """`Popen`'a giden env'leri toplar; süreci `canli_surecler` açmış gibi yapar."""
    gercek_sahte = subprocess.Popen
    envler: list[dict] = []

    def sarmal(argv, **kwargs):
        envler.append(dict(kwargs.get("env") or {}))
        return gercek_sahte(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", sarmal)
    return envler


@pytest.mark.parametrize(
    "origin",
    ["http://127.0.0.1:8790\n", "http://127.0.0.1:8790 ", "http://127.0.0.1:8790/yol"],
)
def test_start_origin_satir_sonu_ve_bosluk_reddedilir(config, env_yakala, origin):
    """`$` satır sonundan önce de eşleşir; `fullmatch` bunu kapatır."""
    durum, hata = launcher.start("atlas", config, frame_origin=origin)
    assert hata is None
    assert "KULE_FRAME_ORIGIN" not in env_yakala[-1]
    assert durum["cerceve"] is False


def test_start_gecerli_origin_cerceve_true(config, env_yakala):
    durum, hata = launcher.start("atlas", config, frame_origin="http://localhost:8790")
    assert hata is None
    assert env_yakala[-1]["KULE_FRAME_ORIGIN"] == "http://localhost:8790"
    assert durum["cerceve"] is True


def test_start_parent_origin_gecersizken_cocuga_gecmez(config, env_yakala, monkeypatch):
    monkeypatch.setenv("KULE_FRAME_ORIGIN", "http://127.0.0.1:1111")
    launcher.start("atlas", config, frame_origin=None)
    assert "KULE_FRAME_ORIGIN" not in env_yakala[-1]


def test_restart_origini_iletir(config, env_yakala):
    launcher.start("atlas", config, frame_origin=None)
    durum, hata = launcher.restart("atlas", config, "http://127.0.0.1:8790")
    assert hata is None
    assert env_yakala[-1]["KULE_FRAME_ORIGIN"] == "http://127.0.0.1:8790"
    assert durum["cerceve"] is True
