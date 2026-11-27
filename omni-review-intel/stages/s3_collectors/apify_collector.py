"""
stages/s3_collectors/apify_collector.py — Optional Apify actor plugin.

Handles Amazon reviews, Instagram, and TikTok comments via Apify actors.
Degrades gracefully when APIFY_API_KEY is not configured.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from config import settings
from core.schemas import DiscoveredSource, RawComment
from core.cache import get_cache

# Apify actor IDs (public actors on the Apify marketplace)
_ACTOR_MAP: dict[str, str] = {
    "Amazon": "junglee/amazon-reviews-scraper",
    "Instagram": "apify/instagram-comment-scraper",
    "TikTok": "clockworks/tiktok-comments-scraper",
}


async def collect_apify(
    source: DiscoveredSource,
) -> tuple[list[RawComment], DiscoveredSource]:
    """Collect comments via Apify actors for Amazon, Instagram, TikTok."""
    if not settings.has_apify:
        logger.info(f"Apify: skipped {source.url} — APIFY_API_KEY not configured")
        source.scrape_status = "skipped"
        source.error_message = "Skipped: Requires Apify key"
        source.collector_used = "apify (skipped)"
        return [], source

    limit = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    cache = get_cache()
    cache_key = f"apify_v2::{source.url}::{settings.max_comments_per_url}"
    cached = cache.get("s3_apify", cache_key)
    if cached:
        comments = [RawComment.model_validate(c) for c in cached][:limit]
        for c in comments:
            c.source_url = source.url
            c.is_target = source.is_target
        source.scrape_status = "success"
        source.collector_used = "apify (cached)"
        source.comments_collected = len(comments)
        return comments, source

    actor_id = _ACTOR_MAP.get(str(source.platform), "")
    if not actor_id:
        source.scrape_status = "skipped"
        source.error_message = f"No Apify actor for platform '{source.platform}'"
        return [], source

    loop = asyncio.get_event_loop()

    def _run_actor():
        try:
            from apify_client import ApifyClient
        except ImportError:
            raise RuntimeError("apify-client not installed")

        client = ApifyClient(settings.apify_api_key)
        run_input: dict = {"startUrls": [{"url": source.url}]}
        if settings.max_comments_per_url > 0:
            run_input["maxItems"] = settings.max_comments_per_url

        run = client.actor(actor_id).call(run_input=run_input, timeout_secs=120)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        return items

    try:
        items = await loop.run_in_executor(None, _run_actor)
    except Exception as e:
        logger.error(f"Apify actor failed for {source.url}: {e}")
        source.scrape_status = "failed"
        source.error_message = str(e)[:300]
        return [], source

    comments: list[RawComment] = []
    platform = source.platform
    limit = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None

    for item in items[:limit]:
        # Normalize across different actor output schemas
        text = (
            item.get("text")
            or item.get("reviewText")
            or item.get("body")
            or item.get("content")
            or ""
        ).strip()
        if not text:
            continue

        rating_raw = item.get("rating") or item.get("starRating")
        try:
            rating = float(rating_raw) if rating_raw is not None else None
        except (TypeError, ValueError):
            rating = None

        comments.append(
            RawComment(
                source_url=source.url,
                platform=platform,
                author=str(item.get("author") or item.get("username") or "Anonymous"),
                published_date=str(item.get("date") or item.get("publishedAt") or ""),
                rating=rating,
                original_text=text,
            )
        )

    source.scrape_status = "success" if comments else "partial"
    source.collector_used = f"apify:{actor_id}"
    source.comments_collected = len(comments)

    if comments:
        cache.set("s3_apify", cache_key, [c.model_dump() for c in comments])

    logger.info(f"Apify: {len(comments)} comments from {source.url} via {actor_id}")
    return comments, source
