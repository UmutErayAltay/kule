"""borsasite (borsa-ai-dashboard) durumu: HTTP health + opsiyonel DB sinyali.

database_url boşsa DB kısmı hiç denenmez (görev tanımı gereği — sadece HTTP
health kullanılır). Doluysa iki ayrı sorgu atılır; biri veya ikisi de
başarısız olursa (tablo yok, bağlantı yok) o alan None kalır, tüm collector
düşmez — pipeline'ın hangi tablosunun eksik/yeniden adlandırılmış olduğunu
bilmiyoruz, bu normal bir durum olabilir.
"""
from __future__ import annotations

import httpx

TIMEOUT = 5.0


def _fetch_last_run_signals(database_url: str) -> dict:
    """trade_decisions ve predictions tablolarından en son created_at'i çeker."""
    result: dict = {"last_trade_decision": None, "last_prediction": None}
    try:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SELECT MAX(created_at) FROM trade_decisions")
                    row = cur.fetchone()
                    result["last_trade_decision"] = row[0].isoformat() if row and row[0] else None
                except Exception:
                    pass  # tablo yok/erişilemedi olabilir

            with conn.cursor() as cur:
                try:
                    cur.execute("SELECT MAX(created_at) FROM predictions")
                    row = cur.fetchone()
                    result["last_prediction"] = row[0].isoformat() if row and row[0] else None
                except Exception:
                    pass
    except Exception:
        pass  # bağlantı kurulamadı, ikisi de None kalır
    return result


def collect(config: dict) -> dict:
    cfg = config.get("borsasite", {})
    health_url = cfg.get("health_url", "")
    database_url = cfg.get("database_url", "")

    out: dict = {"reachable": False, "health": None, "db": None}

    if health_url:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.get(health_url)
                resp.raise_for_status()
                out["health"] = resp.json()
                out["reachable"] = True
        except Exception as e:
            out["error"] = str(e)
    else:
        out["error"] = "config.borsasite.health_url tanımlı değil"

    if database_url:
        out["db"] = _fetch_last_run_signals(database_url)

    return out
