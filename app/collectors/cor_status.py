"""cor (claude-openrouter) HTTP sağlık kontrolü. Dosya parse etmez — cor'un
kendi endpoint'lerine sorar. cor bugün zaten iki kez kapandı, o yüzden
bağlantı hatası burada asla raise etmez, her zaman bir dict döner.
"""
from __future__ import annotations

import httpx

TIMEOUT = 5.0


def collect(config: dict) -> dict:
    base_url = config.get("cor", {}).get("base_url", "").rstrip("/")
    if not base_url:
        return {"reachable": False, "error": "config.cor.base_url tanımlı değil"}

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            health_resp = client.get(f"{base_url}/healthz")
            health_resp.raise_for_status()
            health = health_resp.json()

            dashboard_health = None
            try:
                r = client.get(f"{base_url}/dashboard/api/health")
                r.raise_for_status()
                dashboard_health = r.json()
            except Exception:
                pass  # opsiyonel endpoint, ana health'i düşürmesin

            metrics = None
            try:
                r = client.get(f"{base_url}/dashboard/api/metrics-summary")
                r.raise_for_status()
                metrics = r.json()  # şeklini varsaymadan ham geçir, dashboard yorumlar
            except Exception:
                pass

            return {
                "reachable": True,
                "health": health,
                "dashboard_health": dashboard_health,
                "metrics": metrics,
            }
    except Exception as e:
        return {"reachable": False, "error": str(e)}
