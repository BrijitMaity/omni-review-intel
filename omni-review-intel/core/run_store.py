"""
core/run_store.py — Thread-safe, disk-backed SQLite spool for massive review runs.

Keeps memory flat (< 30 MB RSS) even when scraping and exporting 250,000–500,000+
comments on 1 GB RAM containers, while keeping small/medium runs fast in memory.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections import defaultdict, namedtuple
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

from loguru import logger

from core.schemas import AnalyzedComment, RawComment

_AnalyzedRow = namedtuple(
    "_AnalyzedRow",
    (
        "comment_id",
        "source_url",
        "platform",
        "author",
        "published_date",
        "rating",
        "is_target",
        "original_text",
        "original_language",
        "english_text",
        "sentiment",
        "confidence_score",
        "aspect_tag",
        "is_spam",
        "spam_reason",
    ),
)


class CompactIdSet:
    """Memory-efficient duplicate ID tracker storing 12-byte BLAKE2b digests."""

    __slots__ = ("_digests",)

    def __init__(self, initial: Iterable[str] = ()) -> None:
        self._digests: set[bytes] = set()
        for item in initial:
            self.add(item)

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.blake2b(str(value).encode("utf-8", errors="ignore"), digest_size=12).digest()

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        return self._digest(value) in self._digests

    def add(self, value: str) -> None:
        self._digests.add(self._digest(value))

    def __len__(self) -> int:
        return len(self._digests)


class RunStore:
    """Thread-safe SQLite spool holding RawComment and AnalyzedComment rows for one run."""

    def __init__(self, run_id: str, base_dir: Optional[str | Path] = None) -> None:
        root = Path(base_dir or "outputs") / ".cache" / "runs"
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / f"run_{run_id}.db"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._closed = False
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA temp_store=MEMORY;")
            self._conn.execute("PRAGMA cache_size=-8000;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    author TEXT NOT NULL,
                    published_date TEXT,
                    rating REAL,
                    like_count INTEGER NOT NULL DEFAULT 0,
                    is_target INTEGER NOT NULL DEFAULT 0,
                    original_text TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyzed_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    author TEXT NOT NULL,
                    published_date TEXT,
                    rating REAL,
                    is_target INTEGER NOT NULL DEFAULT 0,
                    original_text TEXT NOT NULL,
                    original_language TEXT NOT NULL,
                    english_text TEXT NOT NULL,
                    sentiment TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    aspect_tag TEXT NOT NULL,
                    is_spam INTEGER NOT NULL DEFAULT 0,
                    spam_reason TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ac_spam_sent ON analyzed_comments(is_spam, sentiment)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ac_spam_tgt ON analyzed_comments(is_spam, is_target)"
            )
            self._conn.commit()

    def add_raw_batch(self, comments: Sequence[RawComment]) -> int:
        """Persist a batch of RawComment objects into SQLite."""
        if not comments or self._closed:
            return 0
        rows = [
            (
                c.comment_id,
                c.source_url,
                str(c.platform),
                c.author or "Anonymous",
                c.published_date,
                c.rating,
                int(getattr(c, "like_count", 0) or 0),
                1 if c.is_target else 0,
                c.original_text,
            )
            for c in comments
        ]
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO raw_comments (
                    comment_id, source_url, platform, author, published_date,
                    rating, like_count, is_target, original_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()
        return len(rows)

    def raw_count(self) -> int:
        if self._closed:
            return 0
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM raw_comments").fetchone()
            return int(row[0]) if row else 0

    def iter_raw_chunks(self, chunk_size: int = 3000) -> Iterator[list[RawComment]]:
        """Yield RawComment chunks in insertion order using keyset pagination."""
        if self._closed:
            return
        last_id = 0
        while True:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT id, comment_id, source_url, platform, author, published_date,
                           rating, like_count, is_target, original_text
                    FROM raw_comments
                    WHERE id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (last_id, chunk_size),
                ).fetchall()
            if not rows:
                break
            last_id = int(rows[-1]["id"])
            chunk: list[RawComment] = []
            for r in rows:
                try:
                    chunk.append(
                        RawComment.model_construct(
                            comment_id=r["comment_id"],
                            source_url=r["source_url"],
                            platform=r["platform"],
                            author=r["author"],
                            published_date=r["published_date"],
                            rating=r["rating"],
                            is_target=bool(r["is_target"]),
                            original_text=r["original_text"],
                        )
                    )
                except Exception:
                    continue
            if chunk:
                yield chunk

    def add_analyzed_batch(self, comments: Sequence[AnalyzedComment]) -> int:
        if not comments or self._closed:
            return 0
        rows = [
            (
                c.comment_id,
                c.source_url,
                str(c.platform),
                c.author or "Anonymous",
                c.published_date,
                c.rating,
                1 if c.is_target else 0,
                c.original_text,
                c.original_language or "English",
                c.english_text or c.original_text,
                c.sentiment,
                float(c.confidence_score),
                c.aspect_tag or "General",
                1 if c.is_spam else 0,
                c.spam_reason or "",
            )
            for c in comments
        ]
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO analyzed_comments (
                    comment_id, source_url, platform, author, published_date,
                    rating, is_target, original_text, original_language,
                    english_text, sentiment, confidence_score, aspect_tag,
                    is_spam, spam_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()
        return len(rows)

    def analyzed_count(
        self,
        valid_only: bool = True,
        sentiment: Optional[str] = None,
        is_target: Optional[bool] = None,
    ) -> int:
        if self._closed:
            return 0
        clauses: list[str] = []
        params: list = []
        if valid_only:
            clauses.append("is_spam = 0")
        if sentiment is not None:
            clauses.append("sentiment = ?")
            params.append(sentiment)
        if is_target is not None:
            clauses.append("is_target = ?")
            params.append(1 if is_target else 0)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM analyzed_comments {where}", tuple(params)
            ).fetchone()
            return int(row[0]) if row else 0

    def iter_analyzed(
        self,
        valid_only: bool = True,
        sentiment: Optional[str] = None,
        is_target: Optional[bool] = None,
        batch_size: int = 3000,
    ) -> Iterator[_AnalyzedRow]:
        """Yield lightweight AnalyzedComment-compatible rows in streaming keyset batches."""
        if self._closed:
            return
        clauses = ["id > ?"]
        base_params: list = []
        if valid_only:
            clauses.append("is_spam = 0")
        if sentiment is not None:
            clauses.append("sentiment = ?")
            base_params.append(sentiment)
        if is_target is not None:
            clauses.append("is_target = ?")
            base_params.append(1 if is_target else 0)
        where_sql = " AND ".join(clauses)
        last_id = 0
        while True:
            params = [last_id, *base_params, batch_size]
            with self._lock:
                # Use positional tuples transiently (much faster than Row/Pydantic overhead per row)
                orig_factory = self._conn.row_factory
                self._conn.row_factory = None
                try:
                    rows = self._conn.execute(
                        f"""
                        SELECT id, comment_id, source_url, platform, author, published_date,
                               rating, is_target, original_text, original_language,
                               english_text, sentiment, confidence_score, aspect_tag,
                               is_spam, spam_reason
                        FROM analyzed_comments
                        WHERE {where_sql}
                        ORDER BY id ASC
                        LIMIT ?
                        """,
                        tuple(params),
                    ).fetchall()
                finally:
                    self._conn.row_factory = orig_factory
            if not rows:
                break
            last_id = int(rows[-1][0])
            for r in rows:
                yield _AnalyzedRow(
                    r[1], r[2], r[3], r[4], r[5], r[6], bool(r[7]),
                    r[8], r[9], r[10], r[11], float(r[12]), r[13], bool(r[14]), r[15],
                )

    def compute_stats_sql(self) -> dict:
        """Compute exact aggregate statistics over all valid analyzed comments via SQL."""
        if self._closed:
            return {}
        with self._lock:
            sent_rows = self._conn.execute(
                """
                SELECT sentiment, COUNT(*) AS cnt
                FROM analyzed_comments
                WHERE is_spam = 0
                GROUP BY sentiment
                """
            ).fetchall()
            pos = neu = neg = 0
            for r in sent_rows:
                s = r["sentiment"]
                cnt = int(r["cnt"])
                if s == "Positive":
                    pos += cnt
                elif s == "Negative":
                    neg += cnt
                else:
                    neu += cnt
            total = pos + neu + neg
            if total == 0:
                return {
                    "total": 0, "positive": 0, "neutral": 0, "negative": 0,
                    "positive_pct": 0.0, "neutral_pct": 0.0, "negative_pct": 0.0,
                    "platform_stats": {}, "aspect_stats": {}, "source_stats": {},
                    "rating_buckets": {i: 0 for i in range(1, 6)},
                    "conf_buckets": {},
                    "target_count": 0, "discovered_count": 0,
                }

            tgt_rows = self._conn.execute(
                "SELECT is_target, COUNT(*) AS cnt FROM analyzed_comments WHERE is_spam = 0 GROUP BY is_target"
            ).fetchall()
            target_count = discovered_count = 0
            for r in tgt_rows:
                if r["is_target"]:
                    target_count = int(r["cnt"])
                else:
                    discovered_count = int(r["cnt"])

            plat_rows = self._conn.execute(
                """
                SELECT platform, sentiment, COUNT(*) AS cnt
                FROM analyzed_comments
                WHERE is_spam = 0
                GROUP BY platform, sentiment
                """
            ).fetchall()
            platform_stats: dict[str, dict[str, int]] = defaultdict(
                lambda: {"total": 0, "positive": 0, "neutral": 0, "negative": 0}
            )
            for r in plat_rows:
                d = platform_stats[str(r["platform"])]
                cnt = int(r["cnt"])
                d["total"] += cnt
                if r["sentiment"] == "Positive":
                    d["positive"] += cnt
                elif r["sentiment"] == "Negative":
                    d["negative"] += cnt
                else:
                    d["neutral"] += cnt

            asp_rows = self._conn.execute(
                """
                SELECT aspect_tag, sentiment, COUNT(*) AS cnt
                FROM analyzed_comments
                WHERE is_spam = 0
                GROUP BY aspect_tag, sentiment
                """
            ).fetchall()
            asp_counts: dict[str, dict[str, int]] = defaultdict(
                lambda: {"total": 0, "Positive": 0, "Neutral": 0, "Negative": 0}
            )
            for r in asp_rows:
                d = asp_counts[str(r["aspect_tag"] or "General")]
                cnt = int(r["cnt"])
                d["total"] += cnt
                s = r["sentiment"] if r["sentiment"] in ("Positive", "Neutral", "Negative") else "Neutral"
                d[s] += cnt
            aspect_stats: dict[str, dict] = {}
            for asp, d in asp_counts.items():
                ta = max(d["total"], 1)
                aspect_stats[asp] = {
                    "mention_count": d["total"],
                    "positive_pct": round(d["Positive"] / ta * 100, 1),
                    "neutral_pct": round(d["Neutral"] / ta * 100, 1),
                    "negative_pct": round(d["Negative"] / ta * 100, 1),
                }

            src_rows = self._conn.execute(
                """
                SELECT source_url, sentiment, COUNT(*) AS cnt
                FROM analyzed_comments
                WHERE is_spam = 0
                GROUP BY source_url, sentiment
                """
            ).fetchall()
            source_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
            for r in src_rows:
                entry = source_stats[r["source_url"]]
                cnt = int(r["cnt"])
                entry[2] += cnt
                if r["sentiment"] == "Positive":
                    entry[0] += cnt
                elif r["sentiment"] == "Negative":
                    entry[1] += cnt

            rate_rows = self._conn.execute(
                """
                SELECT CAST(ROUND(rating) AS INTEGER) AS star, COUNT(*) AS cnt
                FROM analyzed_comments
                WHERE is_spam = 0 AND rating IS NOT NULL
                GROUP BY star
                """
            ).fetchall()
            rating_buckets = {i: 0 for i in range(1, 6)}
            for r in rate_rows:
                b = max(1, min(5, int(r["star"])))
                rating_buckets[b] += int(r["cnt"])

            conf_rows = self._conn.execute(
                """
                SELECT
                    CASE
                        WHEN confidence_score >= 0.85 THEN 'High (≥85%)'
                        WHEN confidence_score >= 0.65 THEN 'Medium (65–84%)'
                        ELSE 'Moderate (<65%)'
                    END AS tier,
                    sentiment,
                    COUNT(*) AS cnt
                FROM analyzed_comments
                WHERE is_spam = 0
                GROUP BY tier, sentiment
                """
            ).fetchall()
            conf_buckets = {
                "High (≥85%)": {"Positive": 0, "Neutral": 0, "Negative": 0},
                "Medium (65–84%)": {"Positive": 0, "Neutral": 0, "Negative": 0},
                "Moderate (<65%)": {"Positive": 0, "Neutral": 0, "Negative": 0},
            }
            for r in conf_rows:
                s = r["sentiment"] if r["sentiment"] in ("Positive", "Neutral", "Negative") else "Neutral"
                conf_buckets[r["tier"]][s] += int(r["cnt"])

            return {
                "total": total,
                "positive": pos,
                "neutral": neu,
                "negative": neg,
                "positive_pct": round(pos / total * 100, 1),
                "neutral_pct": round(neu / total * 100, 1),
                "negative_pct": round(neg / total * 100, 1),
                "platform_stats": dict(platform_stats),
                "aspect_stats": aspect_stats,
                "source_stats": dict(source_stats),
                "rating_buckets": rating_buckets,
                "conf_buckets": conf_buckets,
                "target_count": target_count,
                "discovered_count": discovered_count,
            }

    def sample_analyzed(
        self,
        limit_per_sentiment: int = 10,
        ui_preview_limit: int = 15000,
    ) -> tuple[dict[str, list[str]], list[AnalyzedComment]]:
        """Return (sample_dict_for_llm, stratified_ui_preview_list)."""
        if self._closed:
            return {}, []
        sample_by_sent: dict[str, list[str]] = defaultdict(list)
        preview: list[AnalyzedComment] = []

        # Collect balanced slice: up to ui_preview_limit rows preserving Pos/Neu/Neg/Target/Discovered diversity
        per_bucket = max(1, ui_preview_limit // 4)
        queries = [
            ("Positive", per_bucket * 2),
            ("Negative", per_bucket),
            ("Neutral", per_bucket),
        ]
        seen_row_ids: set[int] = set()
        with self._lock:
            for sent, lim in queries:
                rows = self._conn.execute(
                    """
                    SELECT id, comment_id, source_url, platform, author, published_date,
                           rating, is_target, original_text, original_language,
                           english_text, sentiment, confidence_score, aspect_tag,
                           is_spam, spam_reason
                    FROM analyzed_comments
                    WHERE is_spam = 0 AND sentiment = ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (sent, lim),
                ).fetchall()
                for r in rows:
                    seen_row_ids.add(int(r["id"]))
                    ac = AnalyzedComment.model_construct(
                        comment_id=r["comment_id"],
                        source_url=r["source_url"],
                        platform=r["platform"],
                        author=r["author"],
                        published_date=r["published_date"],
                        rating=r["rating"],
                        is_target=bool(r["is_target"]),
                        original_text=r["original_text"],
                        original_language=r["original_language"],
                        english_text=r["english_text"],
                        sentiment=r["sentiment"],
                        confidence_score=float(r["confidence_score"]),
                        aspect_tag=r["aspect_tag"],
                        is_spam=bool(r["is_spam"]),
                        spam_reason=r["spam_reason"],
                    )
                    preview.append(ac)
                    if len(sample_by_sent[sent]) < limit_per_sentiment:
                        sample_by_sent[sent].append((ac.english_text or "")[:300])

            # Ensure discovered web rows are also represented if present
            if len(preview) < ui_preview_limit:
                rem = ui_preview_limit - len(preview)
                disc_rows = self._conn.execute(
                    """
                    SELECT id, comment_id, source_url, platform, author, published_date,
                           rating, is_target, original_text, original_language,
                           english_text, sentiment, confidence_score, aspect_tag,
                           is_spam, spam_reason
                    FROM analyzed_comments
                    WHERE is_spam = 0 AND is_target = 0
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (min(rem, 2000),),
                ).fetchall()
                for r in disc_rows:
                    if int(r["id"]) in seen_row_ids:
                        continue
                    seen_row_ids.add(int(r["id"]))
                    preview.append(
                        AnalyzedComment.model_construct(
                            comment_id=r["comment_id"],
                            source_url=r["source_url"],
                            platform=r["platform"],
                            author=r["author"],
                            published_date=r["published_date"],
                            rating=r["rating"],
                            is_target=bool(r["is_target"]),
                            original_text=r["original_text"],
                            original_language=r["original_language"],
                            english_text=r["english_text"],
                            sentiment=r["sentiment"],
                            confidence_score=float(r["confidence_score"]),
                            aspect_tag=r["aspect_tag"],
                            is_spam=bool(r["is_spam"]),
                            spam_reason=r["spam_reason"],
                        )
                    )

        return dict(sample_by_sent), preview[:ui_preview_limit]

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._conn.close()
                except Exception:
                    pass

    def cleanup(self) -> None:
        """Close SQLite handle and delete temporary run database files."""
        self.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(f"{self.db_path}{suffix}")
            try:
                if p.exists():
                    p.unlink()
            except Exception as e:
                logger.debug(f"RunStore cleanup skipped {p}: {e}")
