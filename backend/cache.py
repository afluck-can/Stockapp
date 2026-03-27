"""
SQLite-backed cache for stock data.
TTL defaults to 6 hours to balance freshness with API rate limits.
"""

from __future__ import annotations
import sqlite3
import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "stock_cache.db"
DEFAULT_TTL = 6 * 3600  # 6 hours


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_cache (
            ticker   TEXT PRIMARY KEY,
            data     TEXT NOT NULL,
            stored_at REAL NOT NULL
        )
        """
    )
    con.commit()
    return con


def get(ticker: str, ttl: int = DEFAULT_TTL) -> dict | None:
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT data, stored_at FROM stock_cache WHERE ticker = ?",
                (ticker,),
            ).fetchone()
        if row and (time.time() - row[1]) < ttl:
            return json.loads(row[0])
    except Exception as exc:
        logger.warning("Cache read error for %s: %s", ticker, exc)
    return None


def set(ticker: str, data: dict) -> None:
    try:
        payload = json.dumps(data)
        with _conn() as con:
            con.execute(
                """
                INSERT INTO stock_cache (ticker, data, stored_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET data=excluded.data, stored_at=excluded.stored_at
                """,
                (ticker, payload, time.time()),
            )
    except Exception as exc:
        logger.warning("Cache write error for %s: %s", ticker, exc)


def invalidate(ticker: str) -> None:
    try:
        with _conn() as con:
            con.execute("DELETE FROM stock_cache WHERE ticker = ?", (ticker,))
    except Exception as exc:
        logger.warning("Cache invalidate error for %s: %s", ticker, exc)


def clear_all() -> None:
    try:
        with _conn() as con:
            con.execute("DELETE FROM stock_cache")
        logger.info("Cache cleared")
    except Exception as exc:
        logger.warning("Cache clear error: %s", exc)


def stats() -> dict:
    try:
        with _conn() as con:
            total = con.execute("SELECT COUNT(*) FROM stock_cache").fetchone()[0]
            oldest = con.execute("SELECT MIN(stored_at) FROM stock_cache").fetchone()[0]
            newest = con.execute("SELECT MAX(stored_at) FROM stock_cache").fetchone()[0]
        return {
            "total_cached": total,
            "oldest_entry_age_hours": round((time.time() - oldest) / 3600, 1) if oldest else None,
            "newest_entry_age_hours": round((time.time() - newest) / 3600, 1) if newest else None,
        }
    except Exception:
        return {"total_cached": 0}
