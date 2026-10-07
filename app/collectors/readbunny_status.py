"""readbunny durumu: Postgres'e doğrudan bağlanıp `links` tablosundan özet
sinyaller çeker (en son güncelleme, hata/pending sayısı, toplam satır).
Bağlanamazsa reachable=False + error döner, raise etmez.
"""
from __future__ import annotations


def collect(config: dict) -> dict:
    database_url = config.get("readbunny", {}).get("database_url", "")
    if not database_url:
        return {"reachable": False, "error": "config.readbunny.database_url tanımlı değil"}

    try:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(updated_at), "
                    "COUNT(*) FILTER (WHERE status = 'error'), "
                    "COUNT(*) FILTER (WHERE status = 'pending'), "
                    "COUNT(*) "
                    "FROM links"
                )
                last_updated, error_count, pending_count, total_count = cur.fetchone()

        return {
            "reachable": True,
            "last_updated": last_updated.isoformat() if last_updated else None,
            "error_count": error_count,
            "pending_count": pending_count,
            "total_count": total_count,
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)}
