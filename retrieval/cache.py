"""
SQLite cache for fast repeated profile lookups.
TTL: 7 days. Tracks hit counts and cache stats.
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "profile_cache.db"
CACHE_TTL_DAYS = 7


class ProfileCache:
    """SQLite-backed cache for professor profiles."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self._init_db()

    def _init_db(self):
        """Create cache table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profile_cache (
                name_key TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                hit_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def _normalize_key(self, name: str) -> str:
        """Normalize name to a cache key."""
        return name.lower().strip()

    def get(self, name: str) -> dict | None:
        """
        Get a cached profile by name.
        Returns profile dict or None if not cached / expired.
        """
        key = self._normalize_key(name)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT profile_json, fetched_at FROM profile_cache WHERE name_key = ?",
            (key,),
        )
        row = cursor.fetchone()
        if row is None:
            conn.close()
            return None

        profile_json, fetched_at = row
        age_days = (time.time() - fetched_at) / 86400

        if age_days > CACHE_TTL_DAYS:
            # Expired
            conn.execute("DELETE FROM profile_cache WHERE name_key = ?", (key,))
            conn.commit()
            conn.close()
            return None

        # Increment hit count
        conn.execute(
            "UPDATE profile_cache SET hit_count = hit_count + 1 WHERE name_key = ?",
            (key,),
        )
        conn.commit()
        conn.close()

        return json.loads(profile_json)

    def save(self, name: str, profile: dict) -> None:
        """Save a profile to cache."""
        key = self._normalize_key(name)
        profile_json = json.dumps(profile, ensure_ascii=False)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO profile_cache (name_key, profile_json, fetched_at, hit_count)
            VALUES (?, ?, ?, COALESCE(
                (SELECT hit_count FROM profile_cache WHERE name_key = ?), 0
            ))
        """, (key, profile_json, time.time(), key))
        conn.commit()
        conn.close()

    def stats(self) -> dict:
        """Return cache statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT COUNT(*), AVG(? - fetched_at) / 3600, SUM(hit_count) FROM profile_cache",
            (time.time(),),
        )
        row = cursor.fetchone()
        conn.close()
        return {
            "total_cached": row[0] or 0,
            "avg_age_hours": round(row[1] or 0, 1),
            "total_hits": row[2] or 0,
        }

    def clear_expired(self) -> int:
        """Remove expired entries. Returns count deleted."""
        cutoff = time.time() - (CACHE_TTL_DAYS * 86400)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "DELETE FROM profile_cache WHERE fetched_at < ?", (cutoff,)
        )
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count

    def get_top_searched(self, limit: int = 5) -> list:
        """Get the most frequently searched profiles."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT name_key, hit_count FROM profile_cache ORDER BY hit_count DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"name": r[0], "hits": r[1]} for r in rows]
