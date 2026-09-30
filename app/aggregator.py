"""Tüm collector'ları toplayıp tek bir özet dict'e birleştirir.

Her collector kendi try/except'inde izole edilir: biri exception fırlatırsa
(collector'ın kendi içindeki try/except'i her şeyi yakalayamamış olabilir —
örn. config.get() içinde beklenmedik bir tip hatası) o kaynağın alanı
{"error": ...} olur, diğer kaynaklar etkilenmez. Bu panelin tek amacı "her
şey aynı anda görünsün" olduğu için tek kaynağın çökmesi tüm paneli
düşürmemeli.

Basit in-memory TTL cache: art arda hızlı çağrılarda (örn. dashboard'un
birkaç saniyede bir polling yapması) gerçek collector'ları tekrar tetiklemez.
Süreç başına tek bir cache yeterli — kule tek process olarak çalışıyor,
çoklu worker senaryosu bu dalganın kapsamı dışında.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from app.collectors import (
    atlas_status,
    borsasite_status,
    cor_status,
    git_status,
    maintenance_status,
    orkestra_status,
    readbunny_status,
    vault_status,
)

CACHE_TTL_SECONDS = 60

_cache_lock = Lock()
_cache: dict[str, Any] = {"summary": None, "fetched_at": 0.0}


def _run_isolated(name: str, fn: Callable[[dict], Any], config: dict) -> Any:
    try:
        return fn(config)
    except Exception as e:
        return {"error": str(e)}


def collect_all(config: dict) -> dict:
    """Sekiz collector'ı paralel çağırır, her birini izole eder. Cache YOK —
    her çağrıda gerçekten tetiklenir; cache isteyen get_cached_summary kullanır.
    """
    jobs: dict[str, tuple[Callable[[dict], Any], dict]] = {
        "git": (git_status.collect, config),
        "cor": (cor_status.collect, config),
        "borsasite": (borsasite_status.collect, config),
        "readbunny": (readbunny_status.collect, config),
        "vault": (vault_status.collect, config),
        "maintenance": (maintenance_status.collect, config),
        "atlas": (atlas_status.collect, config),
        "orkestra": (orkestra_status.collect, config),
    }

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            executor.submit(_run_isolated, name, fn, cfg): name
            for name, (fn, cfg) in jobs.items()
        }
        for future in futures:
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                # _run_isolated zaten kendi içinde yakalıyor ama Future.result()
                # kendisi de (iptal, vs.) patlayabilir — son güvenlik ağı.
                results[name] = {"error": str(e)}

    results["collected_at"] = time.time()
    return results


def get_cached_summary(config: dict, ttl_seconds: int = CACHE_TTL_SECONDS) -> dict:
    """collect_all sonucunu TTL_SECONDS boyunca önbellekte tutar."""
    now = time.time()
    with _cache_lock:
        cached = _cache["summary"]
        fetched_at = _cache["fetched_at"]
        if cached is not None and (now - fetched_at) < ttl_seconds:
            return cached

    # Kilit dışında hesapla (I/O-bound, uzun sürebilir) — bu arada iki thread
    # aynı anda cache miss yaşayıp iki kez toplayabilir, kabul edilebilir
    # bir maliyet (panel için doğruluk kritik değil, tutarlılık kilidi gerekmez).
    fresh = collect_all(config)
    with _cache_lock:
        _cache["summary"] = fresh
        _cache["fetched_at"] = time.time()
    return fresh
