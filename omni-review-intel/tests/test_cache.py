"""
tests/test_cache.py — Unit tests for the SQLite cache module.
"""

import pytest
import tempfile
from pathlib import Path

from core.cache import Cache


@pytest.fixture
def tmp_cache(tmp_path):
    """Return a fresh Cache backed by a temp file."""
    return Cache(db_path=str(tmp_path / "test_cache.db"))


class TestCache:
    def test_set_and_get(self, tmp_cache):
        tmp_cache.set("test", "key1", {"value": 42})
        result = tmp_cache.get("test", "key1")
        assert result == {"value": 42}

    def test_cache_miss_returns_none(self, tmp_cache):
        assert tmp_cache.get("test", "nonexistent") is None

    def test_has_returns_true(self, tmp_cache):
        tmp_cache.set("ns", "id", [1, 2, 3])
        assert tmp_cache.has("ns", "id") is True
        assert tmp_cache.has("ns", "other") is False

    def test_overwrite(self, tmp_cache):
        tmp_cache.set("ns", "k", "first")
        tmp_cache.set("ns", "k", "second")
        assert tmp_cache.get("ns", "k") == "second"

    def test_delete(self, tmp_cache):
        tmp_cache.set("ns", "k", "data")
        tmp_cache.delete("ns", "k")
        assert tmp_cache.get("ns", "k") is None

    def test_clear_namespace(self, tmp_cache):
        tmp_cache.set("alpha", "x", 1)
        tmp_cache.set("alpha", "y", 2)
        tmp_cache.set("beta", "z", 3)
        deleted = tmp_cache.clear_namespace("alpha")
        assert deleted == 2
        assert tmp_cache.get("beta", "z") == 3

    def test_stats(self, tmp_cache):
        tmp_cache.set("a", "1", "v1")
        tmp_cache.set("b", "2", "v2")
        stats = tmp_cache.stats()
        assert stats["total_entries"] == 2

    def test_serializes_various_types(self, tmp_cache):
        data = {"list": [1, 2, 3], "nested": {"key": "val"}, "num": 3.14}
        tmp_cache.set("x", "complex", data)
        result = tmp_cache.get("x", "complex")
        assert result["list"] == [1, 2, 3]
        assert result["nested"]["key"] == "val"



def test_cache_enables_wal_journal_mode(tmp_path):
    """Ensure SQLite cache initializes with WAL journal mode for concurrency safety."""
    import sqlite3
    from core.cache import Cache

    db_file = tmp_path / "wal_check.db"
    c = Cache(str(db_file))
    c.set("ns", "k1", {"ok": True})
    assert c.get("ns", "k1") == {"ok": True}

    with sqlite3.connect(str(db_file)) as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert str(mode).lower() == "wal"
