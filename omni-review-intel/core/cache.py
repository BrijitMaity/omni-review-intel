"""
core/cache.py — SQLite-backed cache for scraped URLs and LLM batch results.
Caches by URL-hash so reruns never waste API quota or rate-limit credits.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

from loguru import logger

from config import settings


class Cache:
    """Thread-safe SQLite cache with simple key→JSON value semantics."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = Path(db_path or settings.cache_db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Setup ──────────────────────────────────────────────────────────────

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_key ON cache(key)"
            )
            conn.commit()
        logger.debug(f"Cache initialised at {self._db_path}")

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _hash(raw: str) -> str:
        """Return a stable 40-char hex hash for a cache key string."""
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _make_key(namespace: str, identifier: str) -> str:
        return f"{namespace}:{Cache._hash(identifier)}"

    # ── Public API ─────────────────────────────────────────────────────────

    def get(self, namespace: str, identifier: str) -> Optional[Any]:
        """Return cached value (deserialized from JSON) or None if missing."""
        key = self._make_key(namespace, identifier)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            logger.warning(f"Cache: corrupt value for key {key!r}, ignoring")
            return None

    def set(self, namespace: str, identifier: str, value: Any) -> None:
        """Store value (serialized to JSON) under namespace:hash(identifier)."""
        key = self._make_key(namespace, identifier)
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)",
                (key, serialized),
            )
            conn.commit()
        logger.debug(f"Cache SET {key!r} ({len(serialized)} bytes)")

    def delete(self, namespace: str, identifier: str) -> None:
        """Remove a single cached entry."""
        key = self._make_key(namespace, identifier)
        with self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()

    def clear_namespace(self, namespace: str) -> int:
        """Delete all entries in a namespace; returns number deleted."""
        prefix = f"{namespace}:"
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM cache WHERE key LIKE ?", (prefix + "%",)
            )
            conn.commit()
        logger.info(f"Cache: cleared {cur.rowcount} entries in namespace '{namespace}'")
        return cur.rowcount

    def clear_all(self) -> int:
        """Delete all entries in cache; returns number deleted."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM cache")
            conn.commit()
        logger.info(f"Cache: cleared {cur.rowcount} total entries")
        return cur.rowcount

    def has(self, namespace: str, identifier: str) -> bool:
        """Return True if the cache has a value for this key."""
        return self.get(namespace, identifier) is not None

    def stats(self) -> dict[str, int]:
        """Return total row count."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        return {"total_entries": total}


# Module-level singleton
_cache: Optional[Cache] = None


def get_cache() -> Cache:
    """Return (or create) the global Cache singleton."""
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
