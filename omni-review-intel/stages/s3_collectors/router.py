"""
stages/s3_collectors/router.py — Stage 3: Concurrent collector dispatcher.

Routes each DiscoveredSource to the appropriate platform collector,
runs all collections concurrently (bounded by asyncio.Semaphore),
and returns all RawComments + updated DiscoveredSource objects.
"""

from __future__ import annotations

import asyncio
from loguru import logger

from config import settings
from core.schemas import DiscoveredSource, RawComment

from stages.s3_collectors.youtube_collector import collect_youtube
from stages.s3_collectors.reddit_collector import collect_reddit
from stages.s3_collectors.web_collector import collect_web
from stages.s3_collectors.apify_collector import collect_apify
from stages.s3_collectors.instagram_collector import collect_instagram
from stages.s3_collectors.amazon_collector import collect_amazon
from stages.s3_collectors.flipkart_collector import collect_flipkart
from stages.s3_collectors.facebook_collector import collect_facebook
from stages.s3_collectors.tiktok_collector import collect_tiktok


_APIFY_PLATFORMS: set[str] = set()
_YT_PLATFORMS = {"YouTube"}
_REDDIT_PLATFORMS = {"Reddit"}
_IG_PLATFORMS = {"Instagram"}
_AMAZON_PLATFORMS = {"Amazon"}
_FLIPKART_PLATFORMS = {"Flipkart"}
_FB_PLATFORMS = {"Facebook"}
_TIKTOK_PLATFORMS = {"TikTok"}


async def _collect_one(
    source: DiscoveredSource,
    sem: asyncio.Semaphore,
) -> tuple[list[RawComment], DiscoveredSource]:
    """Route one source to its collector, return (comments, updated_source)."""
    async with sem:
        try:
            platform = source.platform
            if platform in _YT_PLATFORMS:
                comments, source = await collect_youtube(source)
            elif platform in _REDDIT_PLATFORMS:
                comments, source = await collect_reddit(source)
            elif platform in _IG_PLATFORMS:
                comments, source = await collect_instagram(source)
            elif platform in _AMAZON_PLATFORMS:
                comments, source = await collect_amazon(source)
            elif platform in _FLIPKART_PLATFORMS:
                comments, source = await collect_flipkart(source)
            elif platform in _FB_PLATFORMS:
                comments, source = await collect_facebook(source)
            elif platform in _TIKTOK_PLATFORMS:
                comments, source = await collect_tiktok(source)
            elif platform in _APIFY_PLATFORMS:
                comments, source = await collect_apify(source)
            else:
                comments, source = await collect_web(source)
        except Exception as e:
            logger.error(f"Collector crashed for {source.url}: {e}")
            source.scrape_status = "failed"
            source.error_message = str(e)[:300]
            comments = []

        # Tag each comment with whether it came from the primary targeted URL
        for c in comments:
            c.is_target = source.is_target

        return comments, source


async def collect_all(
    sources: list[DiscoveredSource],
    progress_cb=None,
) -> tuple[list[RawComment], list[DiscoveredSource]]:
    """
    Concurrently collect comments from all discovered sources while preserving
    original order (so the input URL remains at index 0).

    Returns:
        (all_raw_comments, updated_sources_list)
    """
    sem = asyncio.Semaphore(settings.max_concurrent_scrapes)
    total = len(sources)
    completed_count = 0

    async def _wrapped_collect(src: DiscoveredSource) -> tuple[list[RawComment], DiscoveredSource]:
        nonlocal completed_count
        comments, updated = await _collect_one(src, sem)
        completed_count += 1
        if progress_cb:
            await progress_cb(
                "Stage 3",
                f"Scraped {completed_count}/{total}: [{updated.platform}] {updated.url[:60]} "
                f"→ {len(comments)} comments ({updated.scrape_status})",
            )
        return comments, updated

    tasks = [_wrapped_collect(src) for src in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_comments: list[RawComment] = []
    updated_sources: list[DiscoveredSource] = []

    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            logger.error(f"Collector task raised exception: {r}")
            src = sources[i]
            src.scrape_status = "failed"
            src.error_message = str(r)[:300]
            updated_sources.append(src)
        else:
            comments, source = r
            all_comments.extend(comments)
            updated_sources.append(source)

    logger.info(
        f"Stage 3: collected {len(all_comments)} raw comments "
        f"from {len(updated_sources)} sources"
    )
    return all_comments, updated_sources
