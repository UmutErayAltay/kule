"""Kule'nin yönettiği panel araçları: süreç başlat / durdur / durum.

atlas (8770), orkestra (8780), harita (8900) ve liman (8795) ayrı araç/ikinci panel DEĞİL,
kule'nin içinden yönetilen yardımcı panellerdir: kule her zaman açık, bu
üçü "istediğini aç/kapat". Her biri kendi panelini `web` alt komutuyla
127.0.0.1'de bir portta servis eder; kule o porta HTTP isteği atarak
sağlığını sorar.

Tasarım kararları:

* **Portlar ve alt komutlar SABİT** (görev kararı), ama **exe YOLU
  hardcode değil**: config'deki `<araç>.komut[0]` kullanılır (atlas ve
  orkestra zaten böyle tanımlı), yoksa `shutil.which` ve Python'ın
  user Scripts dizininde aranır. Böylece "hardcode path yok" kuralı
  bozulmaz ve `config.yaml`'a dokunmadan da çalışır.
* **Pid'ler `~/.kule/launched.json`'da** tutulur. Kule yeniden başlayınca
  "bu pid hâlâ çalışıyor mu" diye bakar: çalışıyorsa sahiplenir, ölmüşse
  kaydı düşürür. Sahiplenme pid'e BAKAR AMA tek başına güvenilmez —
  Windows pid'leri yeniden kullanır, yanlış süreci kapatabilirdi; bu
  yüzden sürecin komut satırında da araç adı aranır.
* **Alt süreçler `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` ile**
  başlatılır: kule'nin konsolunu ve Ctrl+C'sini paylaşmazlar (kule
  kapanırken onlar da kapanmalı, `atexit` bunun için var). Bu yüzden
  kapanışta süreçler kule tarafından açıkça kapatılır.
* **Hazır olma (veritabanı/indeks) kontrolü** config'e bırakılmıştır:
  `<araç>.hazirlik_dosyasi` tanımlıysa dosya var mı diye bakılır, yoksa
  `null` ("bilinmiyor") döner — sözleşmedeki "ölçemedim 0 değil" kuralı.
  `config.yaml`'da bu anahtar tanımlı olmadığı için bugün `null` gelir;
  panel bu durumda port sağlığına düşer.
* **Alt sürecin ham metni ASLA dışarı çıkmaz**: stdout/stderr, komut
  yolu ve istisna mesajı panele girmez. Hatalar SABİT Türkçe
  cümlelerdir (bkz. `durum_status.py`'in sabit hata kodu deseni).

Arayüz uçları `app/main.py`'de; bu modül veri almaz, yalnızca süreç
yönetir ve testlerden doğrudan çağrılabilir.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sysconfig
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from app.collectors import durum_status

#: Başlatılan panellerin pid'lerini tutan dosya.
STATE_FILE = Path.home() / ".kule" / "launched.json"

#: Tek process varsayımı geçerli (bkz. CLAUDE.md), yani bir kilit iki
#: eşzamanlı start'ın aynı araca iki süreç açmasını engellemek için yeterli.
#: RLock: `start`/`stop` içinden `status_of` çağrılıyor, o da kilidi alıyor —
#: yeniden girişli kilit olmasa bu iç içe çağrı kilitlenme (deadlock) olurdu.
_lock = threading.RLock()

#: BU sürecin `Popen` ile açtığı pid'ler. Kule kendi çocuğunu tanır —
#: dosyadan okunan bir pid'e güvenmek zorunda kaldığı komut satırı
#: kontrolünü bu süreçler için uygulamaz (bkz. `_pid_alive`).
_kendi_pidlerimiz: set[int] = set()

#: Bu süreçte frame_origin ile başlatılan araç adları. origin
#: verilmişse ekler, stop/shutdown/ölümde çıkar.
_cerceveli: set[str] = set()

#: Son dokunuş zamanları (time.monotonic). touch() ve start() yazar,
#: stop ve ölü süreç kaydı siler.
_son_dokunus: dict[str, float] = {}

HEALTH_TIMEOUT = 3.0
STOP_GRACE_SECONDS = 5.0

# frame_origin kalıbı: sadece http://127.0.0.1:port veya http://localhost:port
_FRAME_ORIGIN_RE = re.compile(r"^http://(127\.0\.0\.1|localhost):[0-9]{1,5}$")

# ---------------------------------------------------------------- hatalar
# SABİT cümleler. Alt sürecin ham metni ASLA buraya girmez.
ERR_AD_YOK = "araç bulunamadı — geçerli adlar: atlas, orkestra, harita, liman"
ERR_EXE_YOK = "bulunamadı: kurulu değil ya da PATH'te değil"
ERR_BASLATILAMADI = "başlatılamadı"
ERR_DURDURULAMADI = "durdurulamadı"

# ------------------------------------------------------------- araçlar
# Portlar ve alt komutlar görev kararıyla SABİT. `{vault}` işareti
# config'deki vault yoluyla değiştirilir; vault yoksa o argüman düşer
# (harita vault verilmezse kendi varsayılanını kullanır).
TOOLS: dict[str, dict[str, Any]] = {
    "atlas": {"port": 8770, "args": ["web", "--port", "8770"]},
    "orkestra": {"port": 8780, "args": ["web", "--port", "8780"]},
    "harita": {"port": 8900, "args": ["web", "{vault}", "--port", "8900"]},
    "liman": {"port": 8795, "args": ["web", "--port", "8795"]},
}

# Windows'ta alt süreç kule'nin konsolundan koparılır; POSIX'te bu
# bayraklar yok.
# CREATE_NO_WINDOW (DETACHED_PROCESS DEĞİL): pip konsol başlatıcıları
# (atlas.exe vb.) asıl python'u alt süreç olarak açar; konsolsuz
# (DETACHED) bırakılırsa o çocuk kendine görünür bir cmd penceresi açar.
# Gizli konsol çocuklara miras kalır, pencere çıkmaz.
if hasattr(subprocess, "CREATE_NO_WINDOW"):
    _DETACH_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
else:  # pragma: no cover - bu makine Windows
    _DETACH_FLAGS = 0


# ------------------------------------------------------------ komut çözümü


def _vault_args(config: dict) -> list[str]:
    """harita'ya verilecek vault pozisyonel argv'si (config'ten, `~` açık).

    Önce `<araç>.vault`, yoksa kule'nin kendi `vault.path` alanı. İkisi de
    yoksa boş liste — harita kendi varsayılanını çözer, kule yol dayatmaz.
    """
    cfg = config.get("harita")
    cfg = cfg if isinstance(cfg, dict) else {}
    value = cfg.get("vault") or config.get("vault", {}).get("path")
    return durum_status._vault_arg(value)


def _resolve_exe(ad: str, config: dict) -> str | None:
    """Araç exe'sinin TAM yolunu çözer; bulunamazsa None.

    Sıra: config'teki `<araç>.komut[0]` → `shutil.which` → Python'ın
    user Scripts dizini. `pip install -e` ile kurulan konsol
    script'leri Windows'ta PATH'te olmayabiliyor, o yüzden son adım şart.
    """
    cfg = config.get(ad)
    cfg = cfg if isinstance(cfg, dict) else {}
    configured = durum_status._command_argv(cfg.get("komut"))
    if configured:
        return configured[0]

    found = shutil.which(ad)
    if found:
        return found

    suffix = ".exe" if hasattr(subprocess, "CREATE_NO_WINDOW") else ""
    for scheme in ("nt_user", "posix_user"):
        try:
            scripts = Path(sysconfig.get_path("scripts", scheme))
        except KeyError:  # pragma: no cover - beklenmeyen platform
            continue
        candidate = scripts / f"{ad}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return None


def tool_command(ad: str, config: dict) -> list[str] | None:
    """`ad` aracının tam argv'sini kurar; exe yoksa None.

    Argümanlar config'ten gelen DEĞERLERLE (vault) birleştirilir ama
    hiçbir zaman shell metnine dönüşmez: `subprocess` daima `shell=False`
    + argv listesiyle çağrılır (bkz. `durum_status.py`).
    """
    tool = TOOLS.get(ad)
    if tool is None:
        return None
    exe = _resolve_exe(ad, config)
    if not exe:
        return None

    vault = _vault_args(config)
    argv = [exe]
    for arg in tool["args"]:
        if arg == "{vault}":
            argv.extend(vault)
        else:
            argv.append(arg)
    return argv


# ------------------------------------------------------------ pid kaydı


def _read_state() -> dict[str, int]:
    """Pid kayıt dosyasını okur; bozuk/eksikse boş sözlük.

    Dosya elle silinmiş ya da yarım yazılmış olabilir — kule bu durumda
    çökmemeli, sadece "hiçbir şey sahiplenmemiş" der.
    """
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        ad: pid
        for ad, pid in raw.items()
        if isinstance(ad, str)
        and isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
    }


def _write_state(state: dict[str, int]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # Yazılamıyorsa süreçler yine de çalışır; sadece yeniden
        # başlatmada sahiplenme yapılamaz. Paneli düşürmeye değmez.
        pass


def _pid_alive(pid: Any, ad: str) -> bool:
    """Pid gerçekten bizim aracımızın süreci mi?

    Kule'nin **bu süreçte** doğrudan `Popen` ile açtığı pid için
    yalnızca "yaşıyor mu" bakılır: pid'i `Popen` bize verdi, yeniden
    kullanım (pid reuse) riski yoktur. Komut satırı kontrolü sadece
    **dosyadan okunan** pid'ler için gerekir — kule yeniden
    başladığında Windows aynı pid'i başka bir sürece vermiş olabilir
    ve kule onu hem "çalışıyor" gösterir hem de kapatırdı.

    Bu ayrım şart: `Popen`'dan gelen pid her zaman komut satırında araç
    adını taşımaz (`komut: ["C:/bin/a.exe"]` gibi bir yol, ya da
    `python -c ...` ile başlatılan panel). Öyle bir yapılandırmada
    başlatılan süreç "çalışmıyor" görünür, `/stop` onu bulamaz ve
    SÜREÇ SIZINTISI olurdu.

    ponytail: komut satırı eşleşmesi tam yola değil araç adına bakar —
    aynı aracın iki farklı kurulumu yanlış değerlendirilmesin diye
    bilinçli olarak gevşek tutuldu.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        if not psutil.pid_exists(pid):
            return False
        if pid in _kendi_pidlerimiz:
            return True
        cmdline = psutil.Process(pid).cmdline()
    except psutil.Error:
        return False
    return ad in " ".join(cmdline)


def _running_pid(state: dict[str, int], ad: str) -> int | None:
    """Kayıtlı pid gerçekten çalışıyorsa onu döner, değilse kaydı düşürür."""
    pid = state.get(ad)
    if pid is None:
        return None
    if _pid_alive(pid, ad):
        return pid
    state.pop(ad, None)
    return None


def _resolve_pid(state: dict[str, int], ad: str) -> int | None:
    """`_running_pid` + ölü kaydı dosyadan TEMİZLE.

    Temizlik "sözlük boş mu"ya değil "kayıt GERÇEKTEN düştü mü"ye bakar:
    tek ölü kayıt varsa `state` zaten boş olduğu için `if state`
    kontrolü yazmayı atlar ve ölü pid dosyada kalır — kule her açılışta
    aynı ölü kaydı tekrar tekrar süpürür, kullanıcı da `/stop` yerine
    "çalışıyor" görür.
    """
    kayitliydi = ad in state
    pid = _running_pid(state, ad)
    if kayitliydi and pid is None:
        _write_state(state)
    return pid


# -------------------------------------------------------------- sağlık


def _http_status(port: int) -> int | None:
    """Porttaki panele basit bir GET atar; durum kodu ya da None.

    Yanıt GÖVDESİ okunmaz/döndürülmez — panelin HTML'i ya da alt
    sürecin bir hata metni kule'ye sızmaz, sadece "200 mi" bilgisi durur.
    """
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=HEALTH_TIMEOUT)
    except Exception:
        return None
    return resp.status_code


def health(ad: str) -> dict[str, Any]:
    """Bir aracın HTTP sağlığı: port, url, durum kodu, erişilebilirlik."""
    port = TOOLS[ad]["port"]
    code = _http_status(port)
    return {
        "port": port,
        "url": f"http://127.0.0.1:{port}/",
        "http_status": code,
        "erisilebilir": code is not None,
    }


# --------------------------------------------------------------- durum


def _readiness(ad: str, config: dict) -> bool | None:
    """Veritabanı/indeks hazır mı? Tanimlanmadıysa `null` (bilinmiyor)."""
    cfg = config.get(ad)
    cfg = cfg if isinstance(cfg, dict) else {}
    path = cfg.get("hazirlik_dosyasi")
    if not isinstance(path, str) or not path.strip():
        return None
    return Path(path.strip()).expanduser().exists()


def status_of(ad: str, config: dict) -> dict[str, Any]:
    """Tek bir aracın durum sözlüğü — panelin ve testlerin ortak şekli."""
    tool = TOOLS[ad]
    with _lock:
        state = _read_state()
        pid = _resolve_pid(state, ad)
        cerceve = ad in _cerceveli and pid is not None

    return {
        "ad": ad,
        "port": tool["port"],
        "url": f"http://127.0.0.1:{tool['port']}/",
        "exe": _resolve_exe(ad, config),
        "komut": tool_command(ad, config),
        "calisiyor": pid is not None,
        "pid": pid,
        "hazir": _readiness(ad, config),
        "cerceve": cerceve,
    }


def list_status(config: dict) -> list[dict[str, Any]]:
    """TÜM araçların durumu — `GET /api/tools` bunu döner."""
    return [status_of(ad, config) for ad in TOOLS]


# ------------------------------------------------------- start / stop


def start(
    ad: str, config: dict, frame_origin: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    r"""Aracı başlatır. `(durum, hata)` döner; hata varsa durum None.

    İdempotent: zaten çalışıyorsa YENİ SÜREÇ AÇMAZ, mevcut durumu
    döner. Eksik exe'yi sessizce geçmez — kullanıcıya ne yapması
    gerektiğini söyleyen sabit bir mesajla 400 döner.

    frame_origin verilmişse ve `^http://(127\.0\.0\.1|localhost):[0-9]{1,5}$`
    kalıbına TAM uyuyorsa alt sürecin env'ine `KULE_FRAME_ORIGIN=<o değer>`
    eklenir (env, mevcut `durum_status._child_env()` sonucunun kopyası üzerine;
    Telegram anahtarları yine çıkarılmış kalmalı). Aksi hâlde env'e HİÇ
    eklenmez (ve parent ortamında KULE_FRAME_ORIGIN varsa o da çocuğa
    geçmemeli: çıkar).
    """
    if ad not in TOOLS:
        return None, ERR_AD_YOK

    with _lock:
        state = _read_state()
        if _running_pid(state, ad) is not None:
            return status_of(ad, config), None  # zaten açık

        argv = tool_command(ad, config)
        if not argv:
            return None, f"{ad}.exe {ERR_EXE_YOK}"

        # frame_origin doğrula ve env hazırla. Parent ortamındaki bir
        # KULE_FRAME_ORIGIN çocuğa GEÇMEZ: yalnız burada doğrulananı veririz.
        env = dict(durum_status._child_env())
        env.pop("KULE_FRAME_ORIGIN", None)
        cerceve_ok = isinstance(frame_origin, str) and bool(
            _FRAME_ORIGIN_RE.fullmatch(frame_origin)
        )
        if cerceve_ok:
            env["KULE_FRAME_ORIGIN"] = frame_origin

        try:
            proc = subprocess.Popen(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=_DETACH_FLAGS,
            )
        except Exception:
            # İstisna metni ASLA dışarı çıkmaz.
            return None, f"{ad} {ERR_BASLATILAMADI}"
        state[ad] = proc.pid
        _write_state(state)
        _kendi_pidlerimiz.add(proc.pid)
        if cerceve_ok:
            _cerceveli.add(ad)
        else:
            _cerceveli.discard(ad)
        _son_dokunus[ad] = time.monotonic()
        return status_of(ad, config), None


def _terminate(pid: int) -> bool:
    """Süreci nazikçe kapatır; dinmiyorsa öldürür. True = kapandı."""
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return True  # zaten yok

    try:
        proc.terminate()
    except psutil.Error:
        return False

    _, alive = psutil.wait_procs([proc], timeout=STOP_GRACE_SECONDS)
    for leftover in alive:
        try:
            leftover.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2)
    return True


def stop(ad: str, config: dict) -> tuple[dict[str, Any] | None, str | None]:
    """Aracı durdurur. `(durum, hata)` döner.

    İdempotent: zaten kapalıysa hata vermez, "kapalı" durumunu döner.
    """
    if ad not in TOOLS:
        return None, ERR_AD_YOK

    with _lock:
        state = _read_state()
        pid = _resolve_pid(state, ad)
        if pid is not None:
            if not _terminate(pid):
                return None, f"{ad} {ERR_DURDURULAMADI}"
            state.pop(ad, None)
            _kendi_pidlerimiz.discard(pid)
            _write_state(state)
        _cerceveli.discard(ad)
        _son_dokunus.pop(ad, None)
        return status_of(ad, config), None


def restart(
    ad: str, config: dict, frame_origin: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Durdur + başlat. Durdurma hata verirse başlatma denenmez."""
    _, hata = stop(ad, config)
    if hata is not None:
        return None, hata
    return start(ad, config, frame_origin)


def shutdown() -> None:
    """Kule kapanırken başlatılmış panelleri de kapatır. **Asla raise etmez.**

    Alt süreçler DETACHED olduğu için Ctrl+C onlara ulaşmaz; bu çağrı
    olmasa her kule kapanışında öksüz süreç kalırdı.

    **Bu modül `atexit`'e KENDİSİ kaydolmaz.** Kayıt `app/cli.py`'nin
    sunucu dalındadır, yani sadece sunucuyu kuran süreçte çalışır.
    Import anında kaydolsaydı `kule --notify-once` ya da `pytest` bu
    modülü import ettiği anda gerçek panelleri kapatma yeteneğine sahip
    olurdu — testlerin ve tek seferlik kontrollerin kule'nin çalışan
    panellerini öldürmesi kabul edilemez.
    """
    try:
        with _lock:
            state = _read_state()
            if not state:
                return
            for ad, pid in list(state.items()):
                if _pid_alive(pid, ad):
                    _terminate(pid)
            _write_state({})
            _kendi_pidlerimiz.clear()
            _cerceveli.clear()
            _son_dokunus.clear()
    except Exception:
        pass


def touch(ad: str) -> None:
    """Aracın son dokunuş zamanını günceller.

    Kullanılmayan araçları `reap_idle` ile kapatmak için kullanılır.
    Aracı bu süreçte başlatmadık (_kendi_pidlerimiz içinde yoksa) sessizce
    no-op yapar.
    """
    if ad not in TOOLS:
        return
    with _lock:
        state = _read_state()
        pid = state.get(ad)
        if pid is not None and pid in _kendi_pidlerimiz:
            _son_dokunus[ad] = time.monotonic()


def _orkestra_mesgul_default(config: dict) -> bool:
    """Varsayılan orkestra meşgul kontrolü: `durum --json` okur.

    `gorev_durum` içinde `calisiyor` veya `onay-bekliyor` > 0 ise True.
    Hata kodu dönerse (okunamadı) -> True (güvenli taraf: kapatma).
    """
    try:
        raw, hata = durum_status.run_durum(config.get("orkestra"), "orkestra")
    except Exception:
        return True
    if hata is not None:
        return True
    if not isinstance(raw, dict):
        return True
    gorev_durum = raw.get("gorev_durum")
    if not isinstance(gorev_durum, dict):
        return True
    calisiyor = gorev_durum.get("calisiyor")
    onay = gorev_durum.get("onay-bekliyor")
    if isinstance(calisiyor, int) and not isinstance(calisiyor, bool) and calisiyor > 0:
        return True
    if isinstance(onay, int) and not isinstance(onay, bool) and onay > 0:
        return True
    return False


def reap_idle(
    config: dict,
    idle_minutes: float,
    simdi: float | None = None,
    orkestra_mesgul: Any | None = None,
) -> list[str]:
    """Boşta kalan araçları kapatır. Kapatılan araç adlarını döner.

    Kural: yalnız `_kendi_pidlerimiz` içindeki pid'e sahip, `_son_dokunus`
    kaydı olan ve `simdi - son_dokunus >= idle_minutes*60` olan araçlar
    `stop()` ile kapatılır. `idle_minutes <= 0` → hiçbir şey yapma.
    Elle/başka süreçten açılmış (kendi_pidlerimiz dışı) araca ASLA dokunma.
    orkestra için kapatmadan önce meşgul mü kontrolü: `orkestra_mesgul`
    (çağrılabilir, test için enjekte edilebilir; None ise varsayılan uygulama)
    True dönerse veya hata verirse (okunamadı) KAPATMA (güvenli taraf).
    `simdi` None ise time.monotonic().
    """
    if idle_minutes <= 0:
        return []

    if simdi is None:
        simdi = time.monotonic()

    if orkestra_mesgul is None:
        orkestra_mesgul = _orkestra_mesgul_default

    # Adayları kilit altında topla; `durum --json` alt süreci (orkestra
    # meşgul kontrolü) kilit DIŞINDA çalışır, yoksa panel uçları o sürece
    # kadar bloklanırdı.
    adaylar: list[str] = []
    with _lock:
        state = _read_state()
        for ad in TOOLS:
            pid = state.get(ad)
            if pid is None or pid not in _kendi_pidlerimiz:
                continue  # yok ya da elle/başka süreçten açılmış: dokunma
            son = _son_dokunus.get(ad)
            if son is None:
                continue  # dokunuş kaydı yok: dokunma
            if simdi - son < idle_minutes * 60:
                continue  # henüz boşta değil
            adaylar.append(ad)

    kapatilan: list[str] = []
    for ad in adaylar:
        if ad == "orkestra":
            try:
                if orkestra_mesgul(config):
                    continue  # meşgul: kapatma
            except Exception:
                continue  # okunamadı: güvenli taraf, kapatma
        _, hata = stop(ad, config)
        if hata is None:
            kapatilan.append(ad)
    return kapatilan


def idle_minutes(config: dict) -> float:
    """Config'ten `araclar.bosta_kapat_dakika` okur; bozuksa varsayılan 15.

    `bool` sayı DEĞİLDİR: `isinstance(True, int)` doğrudur ama "15 dakika"
    demek değildir. `config.get("araclar", {}).get("bosta_kapat_dakika")`
    sayı (bool değil) ve >= 0 ise o, değilse varsayılan 15; 0 = kapalı.
    """
    arac_cfg = config.get("araclar")
    if not isinstance(arac_cfg, dict):
        return 15.0
    val = arac_cfg.get("bosta_kapat_dakika")
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return 15.0
    if val < 0:
        return 15.0
    return float(val)


_reaper_stop_event: threading.Event | None = None
_reaper_thread: threading.Thread | None = None


def start_reaper(
    load_config_fn: Any,
    kontrol_saniye: float = 60.0,
) -> threading.Thread:
    """Boşta kalan araçları periyodik kapatan daemon thread'i başlatır.

    Her turda `load_config_fn()` ile config'i okur (ConfigError/hata →
    o turu atla), `idle_minutes(config)` ile süreyi alır, `reap_idle`
    çağırır; hiçbir istisna thread'i öldürmesin. Thread'i durdurmak için
    `stop_reaper()` çağırın.
    """
    global _reaper_stop_event, _reaper_thread

    if _reaper_thread is not None and _reaper_thread.is_alive():
        return _reaper_thread

    _reaper_stop_event = threading.Event()

    def _run():
        while not _reaper_stop_event.wait(kontrol_saniye):
            try:
                config = load_config_fn()
            except Exception:
                continue  # bu turu atla
            try:
                idle = idle_minutes(config)
                reap_idle(config, idle)
            except Exception:
                pass  # thread'i öldürme

    _reaper_thread = threading.Thread(target=_run, daemon=True, name="kule-reaper")
    _reaper_thread.start()
    return _reaper_thread


def stop_reaper() -> None:
    """Reaper thread'ini durdurur (varsa)."""
    global _reaper_stop_event, _reaper_thread
    if _reaper_stop_event is not None:
        _reaper_stop_event.set()
    if _reaper_thread is not None:
        _reaper_thread.join(timeout=2.0)
    _reaper_stop_event = None
    _reaper_thread = None
