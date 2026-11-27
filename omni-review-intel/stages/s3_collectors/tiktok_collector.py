"""
Stage 3 Collector — TikTok Videos & Comments
============================================
Multi-layer TikTok comment extraction:
  1. Apify API (`APIFY_API_KEY`), when configured.
  2. Public TikTok comment API (`/api/comment/list/`) + embedded HTML JSON
     (`__UNIVERSAL_DATA_FOR_REHYDRATION__`) via Chrome TLS impersonation.
  3. `yt-dlp` fallback for TikTok video metadata & comments.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from config import settings
from core.cache import get_cache
from core.schemas import DiscoveredSource, RawComment
from stages.s3_collectors._http_utils import append_unique_comment, create_chrome_session
from stages.s3_collectors.apify_collector import collect_apify

try:
    import yt_dlp

    _YT_DLP_AVAILABLE = True
except ImportError:
    _YT_DLP_AVAILABLE = False


_TT_VIDEO_ID_RE = re.compile(r"/video/(\d+)")


def _extract_tiktok_video_id(url: str) -> str | None:
    """Extract numeric TikTok aweme/video ID from URL."""
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host and "tiktok.com" not in host:
        return None
    m = _TT_VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _parse_tiktok_comment_list(
    items: list[dict[str, Any]],
    comments: list[RawComment],
    seen_keys: set[str],
    video_id: str,
    source: DiscoveredSource,
    max_items: int | None,
) -> int:
    added = 0
    for item in items:
        if max_items is not None and len(comments) >= max_items:
            break
        if not isinstance(item, dict):
            continue
        user_obj = item.get("user") or {}
        author = (
            user_obj.get("unique_id") or user_obj.get("nickname")
            if isinstance(user_obj, dict)
            else str(item.get("author") or "")
        )
        cid = str(item.get("cid") or item.get("id") or f"tt_{video_id}_{len(comments)}")
        if append_unique_comment(
            comments=comments,
            seen_keys=seen_keys,
            comment_id=cid,
            author=str(author or "TikTok User"),
            text=str(item.get("text") or ""),
            published_date=item.get("create_time") or "",
            rating=None,
            platform="TikTok",
            source=source,
        ):
            added += 1
    return added


def _collect_tiktok_sync(
    source: DiscoveredSource,
    video_id: str | None,
    max_items: int | None,
) -> dict[str, Any]:
    sess = create_chrome_session()
    if sess is None:
        return {"comments": [], "total_reported": 0}

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    total_reported = 0

    try:
        headers = {"Accept-Language": "en-US,en;q=0.9"}
        resp = sess.get(source.url, headers=headers)
        final_url = str(getattr(resp, "url", source.url))
        vid = video_id or _extract_tiktok_video_id(final_url)

        if resp.status_code == 200 and resp.text:
            m_rehyd = re.search(
                r'<script[^>]+id=["\']__UNIVERSAL_DATA_FOR_REHYDRATION__["\'][^>]*>(.*?)</script>',
                resp.text,
                re.DOTALL,
            )
            if m_rehyd:
                try:
                    data = json.loads(m_rehyd.group(1))
                    if not vid:
                        m_vid = re.search(r'"id":"(\d{15,22})"', m_rehyd.group(1))
                        if m_vid:
                            vid = m_vid.group(1)
                    m_cnt = re.search(r'"commentCount":(\d+)', m_rehyd.group(1))
                    if m_cnt:
                        total_reported = max(total_reported, int(m_cnt.group(1)))
                except Exception:
                    pass

        if vid:
            cursor = 0
            max_pages = 5000 if max_items is None else max(100, (max_items // 40) + 5)
            for _ in range(max_pages):
                if max_items is not None and len(comments) >= max_items:
                    break
                api_url = (
                    f"https://www.tiktok.com/api/comment/list/"
                    f"?aid=1988&aweme_id={vid}&count=50&cursor={cursor}"
                )
                c_resp = sess.get(
                    api_url,
                    headers={"Referer": final_url, "Accept": "application/json"},
                )
                if c_resp.status_code != 200:
                    break
                try:
                    payload = c_resp.json()
                except Exception:
                    break
                if payload.get("total") is not None:
                    try:
                        total_reported = max(total_reported, int(payload["total"]))
                    except (TypeError, ValueError):
                        pass
                batch = payload.get("comments") or []
                added = _parse_tiktok_comment_list(batch, comments, seen_keys, vid, source, max_items)
                if not payload.get("has_more") or added == 0:
                    break
                next_cursor = int(payload.get("cursor") or (cursor + len(batch)))
                if next_cursor <= cursor:
                    break
                cursor = next_cursor
                time.sleep(0.3)

    except Exception as e:
        logger.debug(f"TikTok web scrape failed for {source.url}: {e}")
    finally:
        try:
            sess.close()
        except Exception:
            pass

    return {"comments": comments, "total_reported": total_reported}


def _collect_via_ytdlp_tt_sync(source: DiscoveredSource, max_items: int | None) -> list[RawComment]:
    if not _YT_DLP_AVAILABLE:
        return []
    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "getcomments": True,
        "extract_flat": False,
        "socket_timeout": max(settings.request_timeout, 15),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source.url, download=False)
        for item in (info or {}).get("comments") or []:
            if max_items is not None and len(comments) >= max_items:
                break
            append_unique_comment(
                comments=comments,
                seen_keys=seen_keys,
                comment_id=str(item.get("id") or f"tt_yt_{len(comments)}"),
                author=str(item.get("author") or "TikTok User"),
                text=str(item.get("text") or ""),
                published_date=item.get("timestamp") or item.get("time_text"),
                rating=None,
                platform="TikTok",
                source=source,
            )
    except Exception as e:
        logger.debug(f"TikTok yt-dlp fallback failed for {source.url}: {e}")
    return comments


async def collect_tiktok(source: DiscoveredSource) -> tuple[list[RawComment], DiscoveredSource]:
    """Collect TikTok comments without requiring API keys (optional Apify first)."""
    video_id = _extract_tiktok_video_id(source.url)
    max_items: int | None = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    auth_mode = 1 if settings.has_apify else 0
    cache_id = f"tt_v2::{video_id or source.url}::{auth_mode}::{settings.max_comments_per_url}"

    cache = get_cache()
    cached = cache.get("s3_tiktok", cache_id)
    if cached is not None and isinstance(cached, dict):
        restored = [
            RawComment(**{**item, "is_target": source.is_target})
            for item in cached.get("comments", [])
        ]
        updated = source.model_copy(
            update={
                "comments_collected": len(restored),
                "scrape_status": cached.get("scrape_status", "success"),
                "collector_used": cached.get("collector_used", "tiktok (cached)"),
                "error_message": cached.get("error_message", ""),
            }
        )
        return restored, updated

    if settings.has_apify:
        apify_comments, apify_source = await collect_apify(source)
        if apify_comments:
            if max_items is not None and len(apify_comments) > max_items:
                apify_comments = apify_comments[:max_items]
            final_source = apify_source.model_copy(
                update={
                    "comments_collected": len(apify_comments),
                    "is_target": source.is_target,
                }
            )
            cache.set(
                "s3_tiktok",
                cache_id,
                {
                    "comments": [c.model_dump() for c in apify_comments],
                    "scrape_status": final_source.scrape_status,
                    "collector_used": final_source.collector_used,
                    "error_message": final_source.error_message,
                },
            )
            return apify_comments, final_source

    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, _collect_tiktok_sync, source, video_id, max_items)
    comments: list[RawComment] = res["comments"]
    total_reported: int = res["total_reported"]
    collector_name = "tiktok_comment_api"

    if not comments:
        yt_comments = await loop.run_in_executor(None, _collect_via_ytdlp_tt_sync, source, max_items)
        if yt_comments:
            comments = yt_comments
            collector_name = "tiktok_ytdlp"

    if max_items is not None and len(comments) > max_items:
        comments = comments[:max_items]

    if not comments:
        return [], source.model_copy(
            update={
                "scrape_status": "failed",
                "collector_used": collector_name,
                "comments_collected": 0,
                "error_message": "TikTok blocked unauthenticated comment list or video has no comments (add APIFY_API_KEY in Optional Settings for full access)",
            }
        )

    hit_user_cap = max_items is not None and len(comments) >= max_items
    is_partial = bool(total_reported and total_reported > len(comments) and not hit_user_cap)
    status = "partial" if is_partial else "success"
    err = f"Collected {len(comments)} of ~{total_reported:,} TikTok comments" if is_partial else ""

    updated_source = source.model_copy(
        update={
            "scrape_status": status,
            "collector_used": collector_name,
            "comments_collected": len(comments),
            "error_message": err,
        }
    )
    cache.set(
        "s3_tiktok",
        cache_id,
        {
            "comments": [c.model_dump() for c in comments],
            "scrape_status": updated_source.scrape_status,
            "collector_used": updated_source.collector_used,
            "error_message": updated_source.error_message,
        },
    )
    return comments, updated_source
