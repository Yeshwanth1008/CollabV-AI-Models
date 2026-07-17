"""
CollabV AI - Database adapter (SQLite vs PostgreSQL)

Use this module instead of calling collabv.database directly. The backend is
selected via environment variable:

    DB_BACKEND=sqlite     (default)
    DB_BACKEND=postgres   (requires DATABASE_URL)

The synchronous API is preserved: callers don't need to know whether the
backend is async or sync. For Postgres, a private event loop is used to bridge
async calls; for SQLite, the existing sync code is invoked directly.

This means no callers in api.py need to be rewritten the day you flip
DB_BACKEND. Async-native call paths (`*_async`) are also available for
endpoints that want them.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Dict, List, Optional


_BACKEND = os.environ.get("DB_BACKEND", "sqlite").lower()


def backend() -> str:
    return _BACKEND


# ─── Bridge for sync callers when backend is async ────────────────────────

class _AsyncBridge:
    """Owns a dedicated event loop on a background thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="db-adapter-loop")
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


_bridge: Optional[_AsyncBridge] = None


def _get_bridge() -> _AsyncBridge:
    global _bridge
    if _bridge is None:
        _bridge = _AsyncBridge()
    return _bridge


# ─── Initialisation ───────────────────────────────────────────────────────

def init_db(db_path: Optional[str] = None) -> None:
    if _BACKEND == "postgres":
        from . import db_postgres as pg
        _get_bridge().submit(pg.init_db_async())
    else:
        from . import database as sqlite_db
        sqlite_db.init_db(db_path)


# ─── Save / fetch (sync facade) ───────────────────────────────────────────

def save_request(company_id: str, data: dict, db_path: Optional[str] = None) -> None:
    if _BACKEND == "postgres":
        from . import db_postgres as pg
        _get_bridge().submit(pg.save_request_async(company_id, data))
    else:
        from . import database as sqlite_db
        sqlite_db.save_request(company_id, data, db_path)


def save_result(match_id: str, company_id: str, company_name: str,
                results: list, parsed_tags: Optional[dict] = None,
                db_path: Optional[str] = None) -> None:
    if _BACKEND == "postgres":
        from . import db_postgres as pg
        _get_bridge().submit(
            pg.save_result_async(match_id, company_id, company_name, results, parsed_tags)
        )
    else:
        from . import database as sqlite_db
        sqlite_db.save_result(match_id, company_id, company_name, results, parsed_tags, db_path)


def save_feedback(match_id: str, professor_id: str, action: str,
                  reason: str = "", db_path: Optional[str] = None) -> None:
    if _BACKEND == "postgres":
        from . import db_postgres as pg
        _get_bridge().submit(pg.save_feedback_async(match_id, professor_id, action, reason))
    else:
        from . import database as sqlite_db
        sqlite_db.save_feedback(match_id, professor_id, action, reason, db_path)


def get_history(limit: int = 20, db_path: Optional[str] = None) -> List[Dict]:
    if _BACKEND == "postgres":
        from . import db_postgres as pg
        return _get_bridge().submit(pg.get_history_async(limit))
    from . import database as sqlite_db
    return sqlite_db.get_history(limit, db_path)


def get_stats(db_path: Optional[str] = None) -> Dict:
    if _BACKEND == "postgres":
        from . import db_postgres as pg
        return _get_bridge().submit(pg.get_stats_async())
    from . import database as sqlite_db
    return sqlite_db.get_stats(db_path)


__all__ = [
    "backend", "init_db", "save_request", "save_result", "save_feedback",
    "get_history", "get_stats",
]
