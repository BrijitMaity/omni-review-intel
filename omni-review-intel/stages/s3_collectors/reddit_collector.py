"""
stages/s3_collectors/reddit_collector.py — Reddit posts & comments via PRAW.
"""

from __future__ import annotations

import asyncio
import re

from datetime import datetime
import aiohttp
from loguru import logger

from config import settings
from core.schemas import DiscoveredSource, RawComment
from core.cache import get_cache

_SUBMISSION_RE = re.compile(r"reddit\.com/r/[^/]+/comments/([a-z0-9]+)", re.IGNORECASE)


def _extract_submission_id(url: str) -> str | None:
    m = _SUBMISSION_RE.search(url)
    return m.group(1) if m else None


async def collect_reddit(
    source: DiscoveredSource,
) -> tuple[list[RawComment], DiscoveredSource]:
    """Fetch comments for a Reddit submission URL using PRAW or public JSON."""
    limit = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    cache = get_cache()
    cache_key = f"reddit_v2::{source.url}::{settings.max_comments_per_url}"
    cached = cache.get("s3_reddit", cache_key)
    if cached:
        comments = [RawComment.model_validate(c) for c in cached][:limit]
        for c in comments:
            c.source_url = source.url
            c.is_target = source.is_target
        source.scrape_status = "success"
        source.collector_used = "praw (cached)"
        source.comments_collected = len(comments)
        return comments, source

    if not settings.has_reddit:
        comments, source = await _collect_via_public_json(source)
        if comments:
            cache.set("s3_reddit", cache_key, [c.model_dump() for c in comments])
            return comments, source
        logger.warning("Reddit API credentials not configured & public JSON empty — skipping")
        source.scrape_status = "skipped"
        source.error_message = "REDDIT_CLIENT_ID/SECRET not configured"
        return [], source

    submission_id = _extract_submission_id(source.url)

    loop = asyncio.get_event_loop()

    def _fetch():
        try:
            import praw
        except ImportError:
            raise RuntimeError("praw not installed")

        reddit = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )

        comments: list[RawComment] = []
        max_items = settings.max_comments_per_url if settings.max_comments_per_url > 0 else 1000000

        if submission_id:
            submission = reddit.submission(id=submission_id)
        else:
            # Try URL directly
            submission = reddit.submission(url=source.url)

        replace_more_limit = None if settings.max_comments_per_url <= 0 else min(64, max(8, settings.max_comments_per_url // 50))
        submission.comments.replace_more(limit=replace_more_limit)
        for comment in submission.comments.list():
            if len(comments) >= max_items:
                break
            body = (comment.body or "").strip()
            if not body or body in ("[deleted]", "[removed]"):
                continue
            comments.append(
                RawComment(
                    source_url=source.url,
                    platform="Reddit",
                    author=str(comment.author) if comment.author else "Anonymous",
                    published_date=str(comment.created_utc),
                    rating=None,
                    original_text=body,
                )
            )
        return comments

    try:
        comments = await loop.run_in_executor(None, _fetch)
        source.scrape_status = "success"
        source.collector_used = "praw"
        source.comments_collected = len(comments)
        logger.info(f"Reddit: {len(comments)} comments from {source.url}")
        if comments:
            cache.set("s3_reddit", cache_key, [c.model_dump() for c in comments])
        return comments, source
    except Exception as e:
        logger.error(f"PRAW error for {source.url}: {e}")
        source.scrape_status = "failed"
        source.error_message = str(e)[:300]
        return [], source


async def _collect_via_public_json(
    source: DiscoveredSource,
) -> tuple[list[RawComment], DiscoveredSource]:
    """Fetch public Reddit submission + nested reply trees without requiring OAuth API credentials."""
    from stages.s3_collectors._http_utils import append_unique_comment, create_chrome_session

    submission_id = _extract_submission_id(source.url)
    base_clean = source.url.split("?")[0].rstrip("/")
    if not base_clean.endswith(".json"):
        clean_url = f"{base_clean}.json?limit=500&raw_json=1"
    else:
        clean_url = f"{base_clean}?limit=500&raw_json=1"

    data = None
    loop = asyncio.get_running_loop()

    def _fetch_chrome():
        sess = create_chrome_session(timeout=15)
        if sess is None:
            return None
        try:
            r = sess.get(clean_url, headers={"Accept": "application/json"})
            if r.status_code == 200:
                return r.json()
        except Exception:
            return None
        finally:
            try:
                sess.close()
            except Exception:
                pass
        return None

    try:
        data = await loop.run_in_executor(None, _fetch_chrome)
        if data is None:
            connector = aiohttp.TCPConnector()
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"}
            async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
                async with sess.get(clean_url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status == 200:
                        data = await resp.json()

        comments: list[RawComment] = []
        seen_keys: set[str] = set()
        limit = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None

        def _walk_reddit_children(children: list) -> None:
            for child in children or []:
                if limit is not None and len(comments) >= limit:
                    return
                if not isinstance(child, dict) or child.get("kind") != "t1":
                    continue
                cdata = child.get("data") or {}
                body = (cdata.get("body") or "").strip()
                if body and body not in ("[deleted]", "[removed]"):
                    cid = str(cdata.get("id") or f"red_{submission_id}_{len(comments)}")
                    author = str(cdata.get("author") or "RedditUser")
                    append_unique_comment(
                        comments=comments,
                        seen_keys=seen_keys,
                        comment_id=cid,
                        author=author,
                        text=body,
                        published_date=cdata.get("created_utc"),
                        rating=None,
                        platform="Reddit",
                        source=source,
                    )
                replies = cdata.get("replies")
                if isinstance(replies, dict):
                    _walk_reddit_children((replies.get("data") or {}).get("children") or [])

        if isinstance(data, list) and len(data) > 1:
            top_children = (data[1].get("data") or {}).get("children") or []
            _walk_reddit_children(top_children)

        if comments:
            source.scrape_status = "success"
            source.collector_used = "reddit_public_json"
            source.comments_collected = len(comments)
            return comments, source
    except Exception as e:
        logger.debug(f"Public Reddit JSON fetch failed for {source.url}: {e}")
    return [], source
