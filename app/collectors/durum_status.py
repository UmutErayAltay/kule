"""`durum --json` sözleşmesini konuşan üç collector'ın ortak koşucusu.

atlas / orkestra / harita aynı şekilde konuşuyor: kule bir alt süreç
başlatıp (`<araç> durum --json`) stdout'tan TEK bir JSON nesnesi bekliyor,
`surum == 1` ve `kaynak` kendi adı mı diye bakıyor, sayıları doğruluyor.
Aynı subprocess + doğrulama mantığını üç kere yazmak, `maintenance_status`'ın
reddettiği "repo taramasını yeniden yazma" hatasının aynısı olurdu — bu
yüzden kardeş collector'lar burada yalnızca **kendi alanlarını** söylüyor,
koşma/doğrulama mantığı burada TEK yerde.

Güvenlik sözleşmesi (sözleşme.md + CLAUDE.md "Kritik kurallar"):

* Alt sürecin stdout/stderr'i, istisna metni, dosya yolu, komut ya da env
  değişkeni **ASLA** panele ya da Telegram'a girmez. Kayan değerler yalnızca
  doğrulanmış sayılar/kısa etiketler ve SABİT hata kodlarıdır. Tüm hata
  yolları `_fixed_error()` üzerinden döndüğü için bu garanti test edilebilir.
* Geçersiz çıktı (nesne değil, `surum != 1`, yanlış `kaynak`, int olmayan
  sayı) sabit `cikti_gecersiz` üretir. **Bilinmeyen alanlar yok sayılır** —
  sözleşme "yeni alan eklenebilir" diyor, yarın atlas yeni bir sayı eklerse
  kule patlamamalı.
* `KULE_TELEGRAM_*` env değişkenleri alt sürece GEÇİRİLMEZ: kule'nin
  Telegram secret'ı alt süreçten görünmemeli.
* `subprocess.run` daima `shell=False` + argv listesiyle çağrılır; config'ten
  gelen `komut`/`vault` değerleri asla shell metnine dönüşmez.
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
from typing import Any

# ------------------------------------------------------------- sabit hatalar
# Bunlar notifier'a, panele ve Telegram'a giden TEK hata sözlüğü. Alt
# sürecin kendi metni, stderr'i, yolu ya da istisna mesajı ASLA buraya
# girmez — o yüzden hepsi kısa, sabit, elle yazılmış kodlardır.
ERR_CONFIG_YOK = "config_yok"
ERR_KOMUT_YOK = "komut_yok"
ERR_ZAMAN_ASIMI = "zaman_asimi"
ERR_CIKTI_GECERSIZ = "cikti_gecersiz"
ERR_CALISTIRILAMADI = "calistirilamadi"
ERR_BILINMEYEN_HATA = "bilinmeyen_hata"

# Kaynağın kendi `hata` kodları bu listedeki SABİT kodlardan biri olmalı.
# Listede olmayan bir kod (sürüm yükseltmesi, bozuk çıktı, istisna metni)
# ASLA kopyalanmaz — içeriği ne olursa olsun `bilinmeyen_hata` olur. Böylece
# kaynak yanlışlıkla uzun/hata ayrıntılı bir mesaj basarsa panel veya
# Telegram'a sızdırmaz, yalnızca "bir şeyler ters" bilgisi kaybolur.
KNOWN_SOURCE_ERRORS = frozenset(
    {
        "db_yok",  # atlas + orkestra: veritabanı dosyası/bağlantısı yok
        "indeks_yok",  # harita: henüz indeks üretilmemiş
        "okunamadi",  # genel okuma hatası
        "sema_eski",  # orkestra: DB şeması bu sürümden eski (migration bekliyor)
        "yol_gecersiz",  # orkestra: verilen DB yolu çözülemedi
    }
)

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 120.0

# Etiket anahtarları (atlas `bulgu_onem`, orkestra `gorev_durum`) alt
# süreçten gelen metinlerdir: sözleşmede kaynağın kendi enum'larından
# gelmesi bekleniyor ama bu bir söz değil, davranıştır. Uzun ya da kontrol
# karakteri içeren anahtar hem görsel olarak bozar hem de istemciye
# kapatılmamış bir dize taşır — bu yüzden sadece kısa, güvenli etiketler
# kabul edilir.
_LABEL_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,48}$")
MAX_LABEL_ENTRIES = 64

# ISO-8601 zaman damgası. Sözleşme "sadece ISO-8601" diyor; stdout UTF-8
# olduğu için Türkçe karakter burada olamaz. Desene uymayan bir zaman
# damgası `null` olur — ham metin panele sızmaz.
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Alt sürece sızması gereken tek env öneki bu; dışındaki KULE_* değişkenleri
# (örn. KULE_PORT) geçer, yalnızca Telegram secret'ları kesilir.
_TELEGRAM_ENV_PREFIX = "KULE_TELEGRAM_"


# ---------------------------------------------------------------- config'den


def _positive_number(cfg: dict, key: str, default: float) -> float:
    """Eşik değerini okur; eksik/bozuk/sıfıra yakın config için default döner.

    `maintenance_status::_positive_number` ile aynı desen: `True` sayısal bir
    değerdir (bool, int'in alt türü) ama zaman aşımı olarak anlamsız, bu
    yüzden ayrıca reddedilir. NaN `<= 0` kontrolünden geçtiği için ayrıca
    elenir — aksi halde `subprocess.run` NaN timeout ile patlardı.
    """
    value = cfg.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if math.isnan(value) or value <= 0:
        return default
    return float(min(value, MAX_TIMEOUT_SECONDS))


def _command_argv(value: Any) -> list[str] | None:
    """`komut` alanını argv listesine çevirir; bozuk/eksikse None.

    Config'te string ya da liste olabilir. String `shlex.split` ile
    bölünür (`"python -m atlas"` gibi yazılmış komutlar da çalışsın diye),
    liste olduğu gibi kullanılır. Boş elemanlı/geçersiz tipli girdi
    reddedilir — subprocess'a boş argv elemanı geçirmek TypeError fırlatır.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            parts = shlex.split(value.strip())
        except ValueError:
            return None  # kapanmamış tırnak vs.
        return parts or None
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for part in value:
            if isinstance(part, bool) or not isinstance(part, (str, int, float)):
                return None
            parts.append(str(part))
        return parts or None
    return None


def _vault_arg(value: Any) -> list[str]:
    """`harita.vault`'u opsiyonel bir pozisyonel argv'ye çevirir.

    Boşsa/bozuksa elenir: harita `vault` verilmezse kendi varsayılan
    çözümlemesini kullanır (sözleşme §harita), kule yanlış bir yol
    dayatmaz. `~` genişletilir — `config.yaml`'da `~/...` yazılabilir.
    """
    if not isinstance(value, str) or not value.strip():
        return []
    return [os.path.expanduser(value.strip())]


def _child_env() -> dict[str, str]:
    """Alt sürece verilecek env: `KULE_TELEGRAM_*` anahtarları ÇIKARILMIŞ.

    Kule'nin Telegram token'ı alt sürece sızmaz. Testler bunu ortama
    gerçekten bir değer koyup alt sürecin görmediğini doğrular.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_TELEGRAM_ENV_PREFIX)
    }


def _fixed_error(code: str) -> dict:
    """Erişilemeyen kaynağın TEK dönüş şekli.

    Kardeş collector'lar (`cor_status`, `readbunny_status`) erişilemeyen bir
    kaynak için `{"reachable": False, "error": ...}` döner; `notifier` bunu
    "erişilemiyor" diline çevirir. `error` daima bu modülün SABİT kodlarından
    biridir, hiçbir koşulda alt sürecin metni değil.
    """
    return {"reachable": False, "error": code}


# ------------------------------------------------------------- doğrulama


def is_count(value: Any) -> bool:
    """`int` ve `>= 0` mi? `bool` int'in alt türü olduğu için AYRI reddedilir.

    `{"repo_sayisi": true}` geçerli JSON'dur ama "1 repo" demek değildir;
    sözleşme sayıları `int (>=0)` olarak tanımlar, bool saymaz.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def count_or_none(raw: dict, key: str) -> int | None:
    """Sayı alanı: int>=0 ya da `null` (bilinmiyor).

    Sözleşme "bilinmeyen için null (0 DEĞİL)" diyor; 0 göstermek "ölçtüm ve
    sıfır çıktı" anlamına gelirdi, oysa hiç ölçememiş olabiliriz. Panelde
    `null` "bilinmiyor" olarak görünür, Telegram'a zaten hiç girmez.
    """
    value = raw.get(key)
    return value if is_count(value) else None


def counts_or_none(raw: dict, keys: list[str]) -> dict[str, int | None]:
    """Birden çok sayı alanını tek seferde okur."""
    return {key: count_or_none(raw, key) for key in keys}


def flag_or_none(raw: dict, key: str) -> bool | None:
    """`bool` alanı; `null` olabilir (harita `indeks_bayat` hesaplanamayabilir)."""
    value = raw.get(key)
    return value if isinstance(value, bool) else None


def timestamp_or_none(raw: dict, key: str) -> str | None:
    """ISO-8601 zaman damgası; desene uymayan metin `None` olur.

    Ham metin geçirilmez: alt süreç istisna yüzünden beklenmedik bir şey
    basarsa panelde bir yığın izi görünürdü.
    """
    value = raw.get(key)
    if isinstance(value, str) and _ISO_RE.match(value):
        return value
    return None


def date_or_none(value: Any) -> str | None:
    """`YYYY-AA-GG` gün (orkestra `kota.gun`); uymayan metin `None`."""
    return value if isinstance(value, str) and _DATE_RE.match(value) else None


def label_counts_or_none(raw: dict, key: str) -> dict[str, int] | None:
    """`{"<etiket>": <sayı>}` haritasını süzer; harita değilse None.

    Sözlük değilse alan bozuk demektir: sözleşmeye göre bu alan ya harita ya
    hiç yok — panel bunu "veri yok" göstermeli, 0 değil.
    """
    value = raw.get(key)
    if not isinstance(value, dict):
        return None
    out: dict[str, int] = {}
    for raw_label, raw_count in list(value.items())[:MAX_LABEL_ENTRIES]:
        if not isinstance(raw_label, str) or not _LABEL_RE.match(raw_label):
            continue
        if not is_count(raw_count):
            continue
        out[raw_label] = raw_count
    return out


# ------------------------------------------------------------------ koşucu


def run_durum(
    cfg: Any,
    beklenen_kaynak: str,
    *,
    timeout_default: float = DEFAULT_TIMEOUT_SECONDS,
    pozisyonel: Any = None,
) -> tuple[dict | None, str | None]:
    """`<araç> durum --json` alt sürecini çalıştırır, çıktıyı doğrular.

    Args:
        cfg: kaynağın kendi config bloğu; eksik/bozuk tipli olabilir.
        beklenen_kaynak: `kaynak` alanında beklenen sabit ad.
        timeout_default: `zaman_asimi` config'te yoksa kullanılacak saniye.
        pozisyonel: opsiyonel `cfg` → ek argv listesi üreten işlev (harita
            `vault` için).

    Returns:
        `(raw, None)` — doğrulanmış ham JSON sözlüğü (alanlar henüz
        ayrıştırılmamış), ya da `(None, hata_kodu)`. **Asla raise etmez**;
        beklenmedik her istisna `calistirilamadi` olur.
    """
    if not isinstance(cfg, dict):
        cfg = {}

    argv = _command_argv(cfg.get("komut"))
    if not argv:
        # Config eksik: hiç süreç denemez, doğrudan açıklayıcı sabit kod
        # (bkz. `cor_status`/`readbunny_status` deseni).
        return None, ERR_CONFIG_YOK

    argv = list(argv) + ["durum", "--json"]
    if pozisyonel is not None:
        try:
            extra = pozisyonel(cfg)
        except Exception:
            return None, ERR_CONFIG_YOK
        if not isinstance(extra, list):
            return None, ERR_CONFIG_YOK
        argv += extra

    timeout = _positive_number(cfg, "zaman_asimi", timeout_default)

    try:
        proc = subprocess.run(
            argv,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return None, ERR_ZAMAN_ASIMI
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None, ERR_KOMUT_YOK
    except Exception:
        return None, ERR_CALISTIRILAMADI

    # stderr BİLEREK hiç okunmaz: taşıyabileceği dosya yolu/istisna metni
    # panele sızar. Çıkış kodu 0 olsa bile stdout doğrulanır.
    raw = _parse_payload(proc.stdout, beklenen_kaynak)
    if raw is not None:
        return raw, None
    if proc.returncode != 0:
        # Sözleşme §2: hata durumunda da stdout'a JSON basılır, o yüzden
        # buraya düşen çıktı zaten geçersizdir — sabit kod döner.
        return None, ERR_CIKTI_GECERSIZ
    return None, ERR_CIKTI_GECERSIZ


def _parse_payload(stdout: Any, beklenen_kaynak: str) -> dict | None:
    """stdout'u doğrular; geçerliyse ham sözlük, değilse None.

    Sıra önemli: JSON → nesne mi → `surum == 1` → `kaynak` beklenen mi →
    kaynağın kendi `hata` kodu. `surum` için `== 1` YETMEZ: Python'da hem
    `True == 1` hem `1.0 == 1` doğrudur, ama sözleşmede `surum` bir
    sürüm NUMARASI'dır — `1.0`/`true` yazan bir kaynak sözleşmeye uymaz.
    """
    try:
        raw = json.loads(stdout)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    surum = raw.get("surum")
    if isinstance(surum, bool) or not isinstance(surum, int) or surum != 1:
        return None
    if raw.get("kaynak") != beklenen_kaynak:
        return None
    return raw


def source_error(raw: dict) -> str | None:
    """Kaynağın kendi `hata` kodu varsa SABİT karşılığını döner.

    Hata yükünde (sözleşme §2) sayı alanları YOKTUR, bu yüzden bu kontrol
    alan ayrıştırmadan ÖNCE yapılmalıdır. Sabit listedeki bir kodysa aynen
    iletilir (`db_yok`, `indeks_yok`, `okunamadi`); değilse içeriği ne
    olursa olsun `bilinmeyen_hata` olur.
    """
    hata = raw.get("hata")
    if hata is None:
        return None
    if isinstance(hata, str) and hata in KNOWN_SOURCE_ERRORS:
        return hata
    return ERR_BILINMEYEN_HATA
