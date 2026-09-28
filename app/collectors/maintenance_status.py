"""Otomatik bakım tespiti: dolu disk, unutulmuş/yetim süreçler ve hâlâ
commitlenmemiş değişiklik taşıyan repolar.

Kule burada SADECE RAPORLAR: süreç öldürülmez, dosya silinmez, git komutu
çalıştırılmaz. Umut'un elle kontrol etmesi gereken üç şeyi otomatik
görmesini sağlar, müdahale etmez.

Repo taraması TEKRAR YAZILMAZ — `git_status.collect` zaten tüm repoları
özetliyor, burada onun çıktısı üzerinden yalnızca "kirli olanların en son
dosya değişikliğinin mtime'ı" ek bir gezinmeyle ölçülür.

Her alt bölüm (disk / süreçler / git) kendi hatasını kendi `error` alanında
taşır: `psutil` kurulu değilse süreç bölümü düşer, disk ve git bölümleri
yine döner. `psutil` ağır ve opsiyonel bir bağımlılık olduğu için fonksiyon
içinde lazy import edilir (bkz. `borsasite_status.py`, `readbunny_status.py`).
"""
from __future__ import annotations

import math
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from app.collectors import git_status

DEFAULT_DISK_THRESHOLD_PERCENT = 90.0
DEFAULT_STALE_PROCESS_HOURS = 6.0
DEFAULT_STALE_GIT_DAYS = 3.0
DEFAULT_PROCESS_NAMES = ["ollama", "uvicorn", "node"]
FALLBACK_DISK_PATH = "/"

MAX_STALE_PROCESSES = 10
MAX_STALE_REPOS = 10
# Bir repoyu gezerken takılmamak için üst sınır. `node_modules` gibi ağır
# dizinler zaten git_status.SKIP_DIR_NAMES ile atlanıyor, yine de devasa bir
# ağaç panelin toplama turunu bloklamamalı.
MAX_SCANNED_ENTRIES = 20_000
MAX_ERROR_CHARS = 80


def _as_list(value: Any) -> list:
    """Config'teki tek değer/liste/None karışıklığını tek listeye indirger."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _positive_number(cfg: dict, key: str, default: float) -> float:
    """Eşik değerini okur; eksik/bozuk/sıfıra yakın config için default döner.
    `True` bir sayısal değerdir (bool, int'in alt türü) ama eşik olarak
    anlamsız, bu yüzden ayrıca reddedilir. NaN de `<= 0` kontrolünden geçtiği
    için (`float('nan') <= 0` False) ayrıca reddedilir — yoksa eşik
    karşılaştırması sessizce her şeyi eler."""
    value = cfg.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if math.isnan(value) or value <= 0:
        return default
    return float(value)


def _name_patterns(cfg: dict) -> list[str]:
    """İzlenecek süreç adı kalıpları; bozuk/boş config için default.

    YAML'da `process_names: [ollama, null]` gibi bir girdi `str(None)` ile
    `"none"` olurdu ve "none" kelimesi geçen HER süreçle eşleşirdi. `None`/
    boş elemanlar burada elenir.
    """
    names = [
        str(n).strip().lower()
        for n in _as_list(cfg.get("process_names"))
        if n is not None
    ]
    names = [n for n in names if n]
    return names or list(DEFAULT_PROCESS_NAMES)


# ------------------------------------------------------------------ disk


def _disk_paths(config: dict, cfg: dict) -> list[Path]:
    """Kontrol edilecek yollar: `disk_paths`, yoksa `repo_roots`, yoksa `/`."""
    candidates = _as_list(cfg.get("disk_paths")) or _as_list(config.get("repo_roots"))
    if not candidates:
        candidates = [FALLBACK_DISK_PATH]

    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(str(candidate)).expanduser()
        key = str(path)
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        paths.append(path)
    return paths or [Path(FALLBACK_DISK_PATH)]


def _collect_disk(config: dict, cfg: dict) -> dict:
    """`shutil.disk_usage` ile doluluk oranı; eşiği aşanları listeler."""
    threshold = _positive_number(cfg, "disk_threshold_percent", DEFAULT_DISK_THRESHOLD_PERCENT)
    out: dict[str, Any] = {"threshold_percent": threshold, "full": []}
    errors: list[str] = []

    for path in _disk_paths(config, cfg):
        try:
            usage = shutil.disk_usage(path)
        except OSError as e:
            errors.append(f"{path}: {e}")
            continue
        if usage.total <= 0:
            errors.append(f"{path}: toplam boyut sıfır")
            continue
        percent = round(usage.used * 100 / usage.total, 1)
        if percent > threshold:
            out["full"].append(
                {
                    "path": str(path),
                    "percent": percent,
                    "free_gb": round(usage.free / 1024**3, 1),
                }
            )

    out["full"].sort(key=lambda item: item["percent"], reverse=True)
    if errors:
        out["error"] = "; ".join(errors)[:MAX_ERROR_CHARS]
    return out


# -------------------------------------------------------------- süreçler


def _name_matches(name: str, pattern: str) -> bool:
    """Süreç adı kalıba uyuyor mu — `name` ve `pattern` küçük harf olmalı.

    Düz `pattern in name` çok genişti: `node` kalbı `node-red`'i de
    yakalıyordu. Eşleşme bu yüzden adın BAŞINDA olmalı ve hemen ardından
    `-`/`_` GELMEMELİ (kesme/alt çizgi, adın devamıdır: `node-red` ayrı bir
    programdır). Nokta ve rakam serbest — `python3` kalbı `python3.11`'i
    yakalar. `re.escape` şart: kalıp config'den gelir, regex olarak yorumlanırsa
    bozuk ya da beklenmedik biçimde eşleşir.
    """
    return re.match(rf"{re.escape(pattern)}(?![-_])", name) is not None


def _collect_stale_processes(cfg: dict) -> dict:
    """İsim kalıbına uyan ve eşikten uzun süredir çalışan süreçleri listeler.

    KILL YOK — yalnızca rapor. Süreç tarama sırasında ölen/erişilemeyen
    süreçler atlanır, tüm bölüm düşmez.
    """
    min_hours = _positive_number(cfg, "stale_process_hours", DEFAULT_STALE_PROCESS_HOURS)
    patterns = _name_patterns(cfg)
    out: dict[str, Any] = {"min_hours": min_hours, "names": patterns, "items": []}

    try:
        import psutil
    except ImportError as e:
        out["error"] = f"psutil kurulu değil ({e})"
        return out

    try:
        processes = psutil.process_iter(["pid", "name", "create_time"])
    except Exception as e:
        out["error"] = str(e)
        return out

    for proc in processes:
        try:
            info = proc.info
        except Exception:
            continue  # süreç tarama sırasında öldü ya da okunamadı
        if not isinstance(info, dict):
            continue
        name = str(info.get("name") or "")
        lowered = name.lower()
        if not lowered or not any(_name_matches(lowered, p) for p in patterns):
            continue
        created = info.get("create_time")
        if not isinstance(created, (int, float)) or isinstance(created, bool):
            continue
        # Eşik karşılaştırması YUVARLANMIŞ değerle yapılmaz: saatte 0.0005
        # gibi küçük eşiklerde yuvarlama 0.0'a düşüp süreci sessizce eleyebilir.
        hours_raw = (time.time() - created) / 3600
        if hours_raw <= min_hours:
            continue
        hours = round(hours_raw, 1)
        # `cmdline` BİLEREK toplanmaz: süreç komut satırı secret (API key,
        # DB parolası, bearer token) taşıyabilir ve `/api/summary` auth'suz.
        # pid + ad + süre teşhis için yeterli.
        out["items"].append(
            {
                "pid": info.get("pid"),
                "name": name,
                "hours": hours,
            }
        )

    out["items"].sort(key=lambda item: item["hours"], reverse=True)
    out["items"] = out["items"][:MAX_STALE_PROCESSES]
    return out


# ------------------------------------------------------------------ git


def _latest_mtime(repo: Path) -> float | None:
    """Repo çalışma ağacındaki en yeni dosya değişikliğinin epoch mtime'ı.

    `.git` ve `node_modules` gibi dizinler `git_status.SKIP_DIR_NAMES`
    üzerinden atlanır — aynı ağır dizin kuralı iki modülde de geçerli.
    Symlink'ler de atlanır: takip edilirse ağaç repo dışına çıkabilir.
    """
    latest: float | None = None
    scanned = 0
    stack: list[Path] = [repo]
    while stack:
        try:
            entries = list(stack.pop().iterdir())
        except OSError:
            continue
        for entry in entries:
            scanned += 1
            if scanned > MAX_SCANNED_ENTRIES:
                return latest
            try:
                # Symlink'ler ATLANIR: takip edilirse repo ağacı dışına
                # (ör. `/`) çıkılır ve tarama kapsamı dışına yayılır.
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in git_status.SKIP_DIR_NAMES:
                        stack.append(entry)
                    continue
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if latest is None or mtime > latest:
                latest = mtime
    return latest


def _collect_stale_git(config: dict, cfg: dict) -> dict:
    """`git_status.collect` çıktısından "kirli VE eşiği geçen eski" repoları
    süzer — repo taraması git_status'a aittir, burada yeniden yazılmaz."""
    min_days = _positive_number(cfg, "stale_git_days", DEFAULT_STALE_GIT_DAYS)
    out: dict[str, Any] = {"min_days": min_days, "items": []}

    try:
        repos = git_status.collect(config)
    except Exception as e:
        out["error"] = str(e)
        return out

    for summary in repos:
        if not isinstance(summary, dict) or summary.get("error"):
            continue
        if not summary.get("dirty_count"):
            continue  # temiz repo — commitlenmemiş değişiklik yok
        path = summary.get("path")
        if not path:
            continue
        latest = _latest_mtime(Path(path))
        if latest is None:
            continue
        age_days = round((time.time() - latest) / 86400, 1)
        if age_days <= min_days:
            continue
        out["items"].append(
            {
                "name": summary.get("name") or Path(path).name,
                "dirty_count": summary.get("dirty_count"),
                "age_days": age_days,
            }
        )

    out["items"].sort(key=lambda item: item["age_days"], reverse=True)
    out["items"] = out["items"][:MAX_STALE_REPOS]
    return out


# ----------------------------------------------------------------- giriş


def collect(config: dict) -> dict:
    """Üç bakım bölümünü döner: `disk`, `stale_processes`, `stale_git`.

    Bölümler birbirini düşürmez: her biri kendi try/except'inde izole, biri
    beklenmedik bir hatada düşse bile diğer ikisi döner. `collect` hiçbir
    koşulda raise etmez.
    """
    cfg = config.get("maintenance")
    if not isinstance(cfg, dict):
        cfg = {}

    sections: tuple[tuple[str, Callable[..., dict], tuple], ...] = (
        ("disk", _collect_disk, (config, cfg)),
        ("stale_processes", _collect_stale_processes, (cfg,)),
        ("stale_git", _collect_stale_git, (config, cfg)),
    )

    out: dict[str, Any] = {}
    for key, fn, args in sections:
        try:
            out[key] = fn(*args)
        except Exception as e:
            out[key] = {"error": str(e)}
    return out
