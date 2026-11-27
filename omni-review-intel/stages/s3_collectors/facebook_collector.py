"""
Stage 3 Collector — Facebook Posts, Reels & Videos
==================================================
Extracts public comments from Facebook posts, reels, watch URLs, and shares:
  1. Embedded Relay `<script type="application/json">` comment nodes
     (`preferred_body` / `body` + `comment_rendering_instance.comments.total_count`)
     via `curl_cffi` Chrome TLS impersonation (supporting optional `FACEBOOK_COOKIE`).
  2. `yt-dlp` fallback for public Facebook videos/reels.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from config import settings
from core.cache import get_cache
from core.schemas import DiscoveredSource, RawComment
from stages.s3_collectors._http_utils import append_unique_comment, create_chrome_session

try:
    import yt_dlp

    _YT_DLP_AVAILABLE = True
except ImportError:
    _YT_DLP_AVAILABLE = False


_SCRIPT_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_facebook_id(url: str) -> str | None:
    """Extract canonical post/reel/video ID from a Facebook or fb.watch URL."""
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host and not any(tok in host for tok in ("facebook.com", "fb.watch", "fb.com", "fb.me")):
        return None
    if "fb.watch" in host or "fb.me" in host:
        part = parsed.path.strip("/").split("/")[0]
        return part or None
    qs = parse_qs(parsed.query)
    for param in ("v", "story_fbid", "fbid"):
        if qs.get(param):
            return qs[param][0]
    for pattern in (
        r"/(?:reel|videos|posts)/([A-Za-z0-9._-]+)",
        r"/share/(?:p|r|v)/([A-Za-z0-9._-]+)",
        r"/permalink/(\d+)",
    ):
        m = re.search(pattern, parsed.path, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _parse_facebook_html_data(html: str, source: DiscoveredSource) -> dict[str, Any]:
    """Extract embedded Facebook Relay JSON comments & total_count from HTML."""
    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    total_reported = 0

    def _walk(obj: Any) -> None:
        nonlocal total_reported
        if isinstance(obj, dict):
            # Capture reported total count on comment_rendering_instance blocks
            for cri_key in ("comment_rendering_instance", "comment_rendering_instance_for_feed_location"):
                cri = obj.get(cri_key)
                if isinstance(cri, dict):
                    cnt = (cri.get("comments") or {}).get("total_count")
                    if cnt is not None:
                        try:
                            total_reported = max(total_reported, int(cnt))
                        except (TypeError, ValueError):
                            pass

            # Extract comment objects
            body_obj = obj.get("preferred_body") or obj.get("body")
            is_comment_node = (
                isinstance(body_obj, dict)
                and bool(body_obj.get("text"))
                and (
                    obj.get("__typename") == "Comment"
                    or "created_time" in obj
                    or "legacy_fbid" in obj
                    or "comment_action_links" in obj
                )
            )
            if is_comment_node:
                author_obj = obj.get("author")
                if isinstance(author_obj, dict):
                    author = str(author_obj.get("name") or "Facebook User")
                else:
                    author = str(author_obj or "Facebook User")
                pk = str(obj.get("legacy_fbid") or obj.get("id") or f"fb_{len(comments)}")
                append_unique_comment(
                    comments=comments,
                    seen_keys=seen_keys,
                    comment_id=pk,
                    author=author,
                    text=str(body_obj.get("text") or ""),
                    published_date=obj.get("created_time") or "",
                    rating=None,
                    platform="Facebook",
                    source=source,
                )

            for v in obj.values():
                if isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                if isinstance(x, (dict, list)):
                    _walk(x)

    for blob in _SCRIPT_JSON_RE.findall(html or ""):
        if "preferred_body" not in blob and "comment_rendering_instance" not in blob:
            continue
        try:
            payload = json.loads(blob.strip())
            _walk(payload)
        except Exception:
            continue

    login_walled = ("/login/" in (html or "") and len(comments) == 0) or (
        "You must log in to continue" in (html or "") and len(comments) == 0
    )
    return {
        "comments": comments,
        "total_reported": total_reported,
        "login_walled": login_walled,
    }


def _collect_facebook_sync(source: DiscoveredSource) -> dict[str, Any]:
    cookies = settings.facebook_cookie.strip() if settings.has_facebook_cookie else ""
    sess = create_chrome_session(cookies=cookies)
    if sess is None:
        return {"comments": [], "total_reported": 0, "login_walled": False}

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }
    try:
        resp = sess.get(source.url, headers=headers)
        if resp.status_code == 200 and resp.text:
            parsed = _parse_facebook_html_data(resp.text, source)
            if "/login" in str(getattr(resp, "url", "")):
                parsed["login_walled"] = True
            return parsed
    except Exception as e:
        logger.debug(f"Facebook HTML fetch failed for {source.url}: {e}")
    finally:
        try:
            sess.close()
        except Exception:
            pass
    return {"comments": [], "total_reported": 0, "login_walled": False}


def _collect_via_ytdlp_fb_sync(source: DiscoveredSource, max_items: int | None) -> list[RawComment]:
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
                comment_id=str(item.get("id") or f"fb_yt_{len(comments)}"),
                author=str(item.get("author") or "Facebook User"),
                text=str(item.get("text") or ""),
                published_date=item.get("timestamp") or item.get("time_text"),
                rating=None,
                platform="Facebook",
                source=source,
            )
    except Exception as e:
        logger.debug(f"Facebook yt-dlp fallback note for {source.url}: {e}")
    return comments


async def collect_facebook(source: DiscoveredSource) -> tuple[list[RawComment], DiscoveredSource]:
    """Collect public comments from a Facebook post, reel, or video URL."""
    fb_id = _extract_facebook_id(source.url)
    max_items: int | None = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    auth_mode = 1 if settings.has_facebook_cookie else 0
    cache_id = f"fb_v1::{fb_id or source.url}::{auth_mode}::{settings.max_comments_per_url}"

    cache = get_cache()
    cached = cache.get("s3_facebook", cache_id)
    if cached is not None and isinstance(cached, dict):
        restored = [
            RawComment(**{**item, "is_target": source.is_target})
            for item in cached.get("comments", [])
        ]
        updated = source.model_copy(
            update={
                "comments_collected": len(restored),
                "scrape_status": cached.get("scrape_status", "success"),
                "collector_used": cached.get("collector_used", "facebook (cached)"),
                "error_message": cached.get("error_message", ""),
            }
        )
        return restored, updated

    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, _collect_facebook_sync, source)
    comments: list[RawComment] = res["comments"]
    total_reported: int = res["total_reported"]
    login_walled: bool = res["login_walled"]
    collector_name = "facebook_cookie_html" if settings.has_facebook_cookie else "facebook_public_html"

    if not comments:
        yt_comments = await loop.run_in_executor(None, _collect_via_ytdlp_fb_sync, source, max_items)
        if yt_comments:
            comments = yt_comments
            collector_name = "facebook_ytdlp"

    if max_items is not None and len(comments) > max_items:
        comments = comments[:max_items]

    if not comments:
        if login_walled:
            msg = "Blocked: Facebook requires login for comments on this post (add FACEBOOK_COOKIE in Optional Settings)"
        else:
            msg = "No public comments found on Facebook URL (add FACEBOOK_COOKIE in Optional Settings for logged-in access)"
        return [], source.model_copy(
            update={
                "scrape_status": "failed",
                "collector_used": collector_name,
                "comments_collected": 0,
                "error_message": msg,
            }
        )

    hit_user_cap = max_items is not None and len(comments) >= max_items
    is_partial = bool(total_reported and total_reported > len(comments) and not hit_user_cap)
    if is_partial:
        status = "partial"
        err = (
            f"Collected {len(comments)} of ~{total_reported:,} comments "
            "(Facebook limits logged-out pages to initial comment preview; add FACEBOOK_COOKIE in Optional Settings for more)"
        )
    else:
        status = "success"
        err = ""

    updated_source = source.model_copy(
        update={
            "scrape_status": status,
            "collector_used": collector_name,
            "comments_collected": len(comments),
            "error_message": err,
        }
    )
    cache.set(
        "s3_facebook",
        cache_id,
        {
            "comments": [c.model_dump() for c in comments],
            "scrape_status": updated_source.scrape_status,
            "collector_used": updated_source.collector_used,
            "error_message": updated_source.error_message,
        },
    )
    return comments, updated_source
