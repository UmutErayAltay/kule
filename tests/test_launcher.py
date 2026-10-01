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
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import launcher
from app import main as main_module

TOOL_ADLARI = ["atlas", "orkestra", "harita"]


# ------------------------------------------------------------- fixtürler


@pytest.fixture(autouse=True)
def izole_state(tmp_path, monkeypatch):
    """Pid kayıt dosyasını tmp'ye al — gerçek `~/.kule`ye dokunma."""
    state_file = tmp_path / "kule" / "launched.json"
    monkeypatch.setattr(launcher, "STATE_FILE", state_file)
    return state_file


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
    # Portlar config'ten GELMEZ: görev kararı sabit (8770/8780/8900).
    assert launcher.TOOLS["atlas"]["port"] == 8770
    assert launcher.TOOLS["orkestra"]["port"] == 8780
    assert launcher.TOOLS["harita"]["port"] == 8900


def test_tool_command_harita_vault_pozisyonel_alir(config):
    argv = launcher.tool_command("harita", config)

    assert "C:/vault/ornek" in argv
    assert argv[-2:] == ["--port", "8900"]


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


def test_status_alanlari_dogru_konumda(config):
    for arac in launcher.list_status(config):
        assert arac["port"] in (8770, 8780, 8900)
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
        # düğmeler de sayfada olmalı (id düzeni: tool-<eylem>-<araç>)
        assert f'tool-start-{ad}' in html
        assert f'tool-stop-{ad}' in html
        assert f'tool-restart-{ad}' in html
