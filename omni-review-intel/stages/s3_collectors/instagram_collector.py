"""
Stage 3 Collector — Instagram Posts, Reels & TV
==================================================
Multi-layer Instagram comment & post extraction:
  1. Apify API (`APIFY_API_KEY`), when configured (highest coverage).
  2. Authenticated Instagram Web API (`INSTAGRAM_SESSION_ID` cookie),
     paginating parent comments + threaded child replies up to `max_comments_per_url`.
  3. Zero-key Public HTML JSON extraction (`xig_polaris_media`,
     `xdt_api__v1__media__shortcode__web_info`, `edge_media_to_parent_comment`)
     via `curl_cffi` Chrome TLS impersonation (IPv4).
  4. `yt-dlp` fallback for public reels/videos when HTML JSON is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from config import settings
from core.cache import get_cache
from core.schemas import DiscoveredSource, RawComment
from stages.s3_collectors.apify_collector import collect_apify

try:
    from curl_cffi import CurlOpt
    from curl_cffi import requests as curl_requests

    _CURL_AVAILABLE = True
except ImportError:
    _CURL_AVAILABLE = False

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

try:
    import yt_dlp

    _YT_DLP_AVAILABLE = True
except ImportError:
    _YT_DLP_AVAILABLE = False


_IG_SHORTCODE_RE = re.compile(
    r"(?:instagram\.com|instagr\.am)/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]{5,})",
    re.IGNORECASE,
)
_SCRIPT_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_IG_APP_ID = "936619743392459"

_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}


def _extract_shortcode(url: str) -> str | None:
    """Extract the Instagram media shortcode from /p/, /reel/, /reels/, or /tv/ URLs."""
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host and "instagram.com" not in host and "instagr.am" not in host:
        return None
    match = _IG_SHORTCODE_RE.search(url)
    return match.group(1) if match else None


def _normalize_session_id(raw: str) -> str:
    """Normalize raw cookie value or full 'sessionid=...' header snippet."""
    value = (raw or "").strip().strip('"').strip("'")
    if not value:
        return ""
    if "sessionid=" in value:
        for part in value.split(";"):
            part = part.strip()
            if part.startswith("sessionid="):
                value = part.split("=", 1)[1].strip()
                break
    return value


def _format_ig_timestamp(ts: Any) -> str:
    """Convert Unix timestamp or date string into YYYY-MM-DD."""
    if ts is None or ts == "":
        return ""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    text = str(ts).strip()
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    return text[:10]


def _make_raw_comment(
    comment_id: str,
    author: str,
    text: str,
    published_date: str,
    source: DiscoveredSource,
) -> RawComment:
    return RawComment(
        comment_id=comment_id,
        source_url=source.url,
        platform="Instagram",
        author=(author or "Instagram User").strip(),
        published_date=published_date,
        rating=None,
        is_target=source.is_target,
        original_text=text.strip(),
    )


def _append_unique_comment(
    comments: list[RawComment],
    seen_keys: set[str],
    comment_pk: Any,
    author: str,
    text: str,
    created_at: Any,
    shortcode: str,
    source: DiscoveredSource,
) -> bool:
    clean_text = (text or "").strip()
    if not clean_text:
        return False

    clean_author = (author or "Instagram User").strip()
    pk_str = str(comment_pk).strip() if comment_pk is not None else ""
    dedup_key = f"pk:{pk_str}" if pk_str else f"txt:{clean_author.lower()}::{clean_text}"
    # Also guard identical (author, text) duplicates across mixed HTML/API sources
    text_key = f"txt:{clean_author.lower()}::{clean_text}"
    if dedup_key in seen_keys or text_key in seen_keys:
        return False

    seen_keys.add(dedup_key)
    seen_keys.add(text_key)

    cid = f"ig_{shortcode}_{pk_str or len(comments)}"
    comments.append(
        _make_raw_comment(
            comment_id=cid,
            author=clean_author,
            text=clean_text,
            published_date=_format_ig_timestamp(created_at),
            source=source,
        )
    )
    return True


def _extract_comment_node(
    node: dict[str, Any],
    comments: list[RawComment],
    seen_keys: set[str],
    shortcode: str,
    source: DiscoveredSource,
) -> None:
    if not isinstance(node, dict):
        return
    user_obj = node.get("user") or node.get("owner") or {}
    author = user_obj.get("username") if isinstance(user_obj, dict) else ""
    if not author:
        author = node.get("username") or ""
    _append_unique_comment(
        comments=comments,
        seen_keys=seen_keys,
        comment_pk=node.get("pk") or node.get("id"),
        author=str(author or ""),
        text=str(node.get("text") or ""),
        created_at=node.get("created_at") or node.get("created_at_utc"),
        shortcode=shortcode,
        source=source,
    )


def _walk_json_for_instagram_data(
    obj: Any,
    state: dict[str, Any],
    comments: list[RawComment],
    seen_keys: set[str],
    shortcode: str,
    source: DiscoveredSource,
) -> None:
    """Recursively inspect embedded Instagram JSON payloads for media ID, counts, and comments."""
    if isinstance(obj, dict):
        # 1. Polaris GraphQL payload (`xig_polaris_media`)
        if "comments_connection" in obj and isinstance(obj["comments_connection"], dict):
            if not state["media_id"] and obj.get("pk"):
                state["media_id"] = str(obj["pk"])
            if obj.get("comment_count") is not None:
                try:
                    state["total_reported"] = max(state["total_reported"], int(obj["comment_count"]))
                except (TypeError, ValueError):
                    pass
            edges = obj["comments_connection"].get("edges") or []
            for edge in edges:
                if isinstance(edge, dict):
                    _extract_comment_node(edge.get("node") or edge, comments, seen_keys, shortcode, source)

        # 2. Relay/GraphQL comment containers (`edge_media_to_parent_comment` / `edge_media_to_comment`)
        for edge_key in ("edge_media_to_parent_comment", "edge_media_to_comment", "edge_threaded_comments"):
            container = obj.get(edge_key)
            if isinstance(container, dict):
                if container.get("count") is not None and edge_key != "edge_threaded_comments":
                    try:
                        state["total_reported"] = max(state["total_reported"], int(container["count"]))
                    except (TypeError, ValueError):
                        pass
                for edge in container.get("edges") or []:
                    if isinstance(edge, dict):
                        node = edge.get("node") or edge
                        _extract_comment_node(node, comments, seen_keys, shortcode, source)
                        if isinstance(node, dict) and "edge_threaded_comments" in node:
                            _walk_json_for_instagram_data(
                                {"edge_threaded_comments": node["edge_threaded_comments"]},
                                state,
                                comments,
                                seen_keys,
                                shortcode,
                                source,
                            )

        # 3. `xdt_api__v1__media__shortcode__web_info` preview_comments
        if "preview_comments" in obj and isinstance(obj["preview_comments"], list):
            for item in obj["preview_comments"]:
                _extract_comment_node(item, comments, seen_keys, shortcode, source)

        # Capture media id & comment_count when present at item level
        if not state["media_id"] and obj.get("code") == shortcode and obj.get("pk"):
            state["media_id"] = str(obj["pk"])
        if obj.get("comment_count") is not None and (
            obj.get("code") == shortcode or "caption" in obj or "comments_connection" in obj
        ):
            try:
                state["total_reported"] = max(state["total_reported"], int(obj["comment_count"]))
            except (TypeError, ValueError):
                pass

        for value in obj.values():
            if isinstance(value, (dict, list)):
                _walk_json_for_instagram_data(value, state, comments, seen_keys, shortcode, source)

    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _walk_json_for_instagram_data(item, state, comments, seen_keys, shortcode, source)


def _parse_public_html_data(
    html: str,
    shortcode: str,
    source: DiscoveredSource,
) -> dict[str, Any]:
    """
    Parse public Instagram post/reel HTML for embedded comments, media_id,
    CSRF token, and reported total comment count.
    """
    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    state: dict[str, Any] = {"media_id": None, "total_reported": 0}

    for blob in _SCRIPT_JSON_RE.findall(html or ""):
        blob = blob.strip()
        if not blob or (
            "comments_connection" not in blob
            and "edge_media_to" not in blob
            and "preview_comments" not in blob
            and "media_id" not in blob
        ):
            continue
        try:
            payload = json.loads(blob)
        except Exception:
            continue
        _walk_json_for_instagram_data(payload, state, comments, seen_keys, shortcode, source)

    if not state["media_id"]:
        for pattern in (
            r'"media_id"\s*:\s*"(\d+)"',
            r"instagram://media\?id=(\d+)",
            r'"pk"\s*:\s*"(\d+)"[^}]*"code"\s*:\s*"' + re.escape(shortcode) + r'"',
        ):
            m = re.search(pattern, html or "")
            if m:
                state["media_id"] = m.group(1)
                break

    if not state["total_reported"]:
        counts = [int(c) for c in re.findall(r'"comment_count"\s*:\s*(\d+)', html or "")]
        if counts:
            state["total_reported"] = max(counts)

    csrf_match = re.search(r'"csrf_token"\s*:\s*"([^"]+)"', html or "")
    csrf_token = csrf_match.group(1) if csrf_match else ""

    has_media_payload = bool(state["media_id"] or state["total_reported"])
    login_walled = (
        "__coig_login" in (html or "")
        or "PolarisLoginPage" in (html or "")
        or ("/accounts/login/" in (html or "") and not has_media_payload)
    ) and len(comments) == 0

    return {
        "comments": comments,
        "media_id": state["media_id"],
        "total_reported": state["total_reported"],
        "csrf_token": csrf_token,
        "login_walled": login_walled,
    }


def _create_http_session(cookies: dict[str, str] | None = None):
    """Create a Chrome-impersonated IPv4 session with automatic httpx 403/429 failover."""
    from stages.s3_collectors._http_utils import create_chrome_session

    return create_chrome_session(
        cookies=cookies or {},
        timeout=float(max(settings.request_timeout, 15)),
    )


def _parse_v1_comment_list(
    items: list[dict[str, Any]],
    comments: list[RawComment],
    seen_keys: set[str],
    shortcode: str,
    source: DiscoveredSource,
    max_items: int | None,
) -> tuple[int, list[tuple[str, int, int]]]:
    """Append v1 API comment items (plus inline preview replies) and track parents needing child fetch."""
    added = 0
    parents_with_more_replies: list[tuple[str, int, int]] = []

    for item in items:
        if max_items is not None and len(comments) >= max_items:
            break
        if not isinstance(item, dict):
            continue

        user_obj = item.get("user") or {}
        author = user_obj.get("username") if isinstance(user_obj, dict) else ""
        pk = item.get("pk") or item.get("id")
        if _append_unique_comment(
            comments=comments,
            seen_keys=seen_keys,
            comment_pk=pk,
            author=str(author or ""),
            text=str(item.get("text") or ""),
            created_at=item.get("created_at_utc") or item.get("created_at"),
            shortcode=shortcode,
            source=source,
        ):
            added += 1

        preview_children = item.get("preview_child_comments") or []
        for child in preview_children:
            if max_items is not None and len(comments) >= max_items:
                break
            if not isinstance(child, dict):
                continue
            c_user = child.get("user") or {}
            c_author = c_user.get("username") if isinstance(c_user, dict) else ""
            if _append_unique_comment(
                comments=comments,
                seen_keys=seen_keys,
                comment_pk=child.get("pk") or child.get("id"),
                author=str(c_author or ""),
                text=str(child.get("text") or ""),
                created_at=child.get("created_at_utc") or child.get("created_at"),
                shortcode=shortcode,
                source=source,
            ):
                added += 1

        child_count = int(item.get("child_comment_count") or 0)
        if pk and child_count > len(preview_children):
            parents_with_more_replies.append((str(pk), child_count, len(preview_children)))

    return added, parents_with_more_replies


def _fetch_post_html_sync(
    url: str,
    shortcode: str | None,
    session_id: str = "",
) -> tuple[str, str, str]:
    """Fetch Instagram post/reel HTML synchronously and return (html, final_url, csrftoken)."""
    cookies = {"sessionid": session_id} if session_id else None
    client = _create_http_session(cookies=cookies)
    if client is None:
        return "", url, ""

    candidate_urls: list[str] = []
    if shortcode:
        candidate_urls.append(f"https://www.instagram.com/p/{shortcode}/")
        candidate_urls.append(f"https://www.instagram.com/reel/{shortcode}/")
    if url not in candidate_urls:
        candidate_urls.append(url)

    try:
        for target_url in candidate_urls:
            resp = client.get(target_url, headers=_BROWSER_HEADERS)
            if resp.status_code == 200 and resp.text:
                csrf = ""
                try:
                    csrf = resp.cookies.get("csrftoken") or client.cookies.get("csrftoken") or ""
                except Exception:
                    csrf = ""
                return resp.text, str(getattr(resp, "url", target_url)), csrf
    except Exception as e:
        logger.debug(f"Instagram HTML fetch failed for {url}: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass
    return "", url, ""


def _collect_via_session_api_sync(
    source: DiscoveredSource,
    shortcode: str,
    session_id: str,
    max_items: int | None,
) -> dict[str, Any]:
    """
    Use user's `sessionid` cookie to paginate Instagram's `/api/v1/media/{media_id}/comments/`
    and child comment threads up to `max_items` (or all if `max_items is None`).
    """
    cookies = {"sessionid": session_id}
    client = _create_http_session(cookies=cookies)
    if client is None:
        return {"comments": [], "total_reported": 0, "session_rejected": False}

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    total_reported = 0
    session_rejected = False
    media_id: str | None = None
    csrf_token = ""

    try:
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        html_resp = client.get(post_url, headers=_BROWSER_HEADERS)
        try:
            csrf_token = html_resp.cookies.get("csrftoken") or client.cookies.get("csrftoken") or ""
        except Exception:
            csrf_token = ""

        if html_resp.status_code == 200 and html_resp.text:
            parsed = _parse_public_html_data(html_resp.text, shortcode, source)
            media_id = parsed["media_id"]
            total_reported = parsed["total_reported"]
            csrf_token = csrf_token or parsed["csrf_token"]
            if "/accounts/login" in str(getattr(html_resp, "url", "")):
                session_rejected = True
            for c in parsed["comments"]:
                if max_items is not None and len(comments) >= max_items:
                    break
                dedup_text = f"txt:{c.author.lower()}::{c.original_text}"
                if dedup_text not in seen_keys:
                    seen_keys.add(dedup_text)
                    seen_keys.add(f"pk:{c.comment_id.rsplit('_', 1)[-1]}")
                    comments.append(c)

        if not media_id:
            return {
                "comments": comments,
                "total_reported": total_reported,
                "session_rejected": session_rejected,
            }

        api_headers = {
            "X-IG-App-ID": _IG_APP_ID,
            "X-CSRFToken": csrf_token or "missing",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": post_url,
            "Accept": "*/*",
        }

        cursor_param: tuple[str, str] | None = None
        seen_cursors: set[str] = set()
        pending_child_fetches: list[tuple[str, int, int]] = []
        max_pages = 5000 if max_items is None else max(250, (max_items // 10) + 10)

        for page_idx in range(max_pages):
            if max_items is not None and len(comments) >= max_items:
                break

            params: dict[str, str] = {
                "can_support_threading": "true",
                "permalink_enabled": "false",
            }
            if cursor_param:
                params[cursor_param[0]] = cursor_param[1]

            api_url = f"https://www.instagram.com/api/v1/media/{media_id}/comments/"
            resp = client.get(api_url, params=params, headers=api_headers)
            ctype = (resp.headers.get("content-type") or "").lower()

            if resp.status_code in (401, 403) or "text/html" in ctype:
                session_rejected = True
                logger.warning(
                    f"Instagram session API rejected/redirected on page {page_idx + 1} "
                    f"(status={resp.status_code})"
                )
                break

            if resp.status_code != 200:
                logger.debug(f"Instagram v1 API HTTP {resp.status_code} on page {page_idx + 1}")
                break

            try:
                data = resp.json()
            except Exception:
                break

            if data.get("require_login") or data.get("status") == "fail":
                session_rejected = bool(data.get("require_login"))
                break

            if data.get("comment_count") is not None:
                try:
                    total_reported = max(total_reported, int(data["comment_count"]))
                except (TypeError, ValueError):
                    pass

            batch = data.get("comments") or []
            added, child_targets = _parse_v1_comment_list(
                batch, comments, seen_keys, shortcode, source, max_items
            )
            pending_child_fetches.extend(child_targets)

            next_min = data.get("next_min_id")
            next_max = data.get("next_max_id")
            if next_min:
                next_pair = ("min_id", str(next_min))
            elif next_max:
                next_pair = ("max_id", str(next_max))
            else:
                break

            if next_pair[1] in seen_cursors or (added == 0 and not batch):
                break
            seen_cursors.add(next_pair[1])
            cursor_param = next_pair
            time.sleep(0.35)

        # Fetch expanded child replies for parents whose preview was truncated
        max_child_pages = 500 if max_items is None else max(20, (max_items // 5) + 5)
        for parent_pk, _child_count, _ in pending_child_fetches:
            if max_items is not None and len(comments) >= max_items:
                break
            child_cursor: tuple[str, str] | None = None
            seen_child_cursors: set[str] = set()
            for _ in range(max_child_pages):
                if max_items is not None and len(comments) >= max_items:
                    break
                c_params: dict[str, str] = {}
                if child_cursor:
                    c_params[child_cursor[0]] = child_cursor[1]
                c_url = f"https://www.instagram.com/api/v1/media/{media_id}/comments/{parent_pk}/child_comments/"
                c_resp = client.get(c_url, params=c_params, headers=api_headers)
                if c_resp.status_code != 200 or "text/html" in (c_resp.headers.get("content-type") or "").lower():
                    break
                try:
                    c_data = c_resp.json()
                except Exception:
                    break
                child_items = c_data.get("child_comments") or c_data.get("comments") or []
                c_added, _ = _parse_v1_comment_list(
                    child_items, comments, seen_keys, shortcode, source, max_items
                )
                c_next_min = c_data.get("next_min_child_cursor")
                c_next_max = c_data.get("next_max_child_cursor")
                if c_next_min:
                    c_pair = ("min_id", str(c_next_min))
                elif c_next_max:
                    c_pair = ("max_id", str(c_next_max))
                else:
                    break
                if c_pair[1] in seen_child_cursors or (c_added == 0 and not child_items):
                    break
                seen_child_cursors.add(c_pair[1])
                child_cursor = c_pair
                time.sleep(0.25)

    except Exception as e:
        logger.warning(f"Instagram session API error for {shortcode}: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass

    return {
        "comments": comments,
        "total_reported": total_reported,
        "session_rejected": session_rejected,
    }


def _collect_via_ytdlp_ig_sync(
    url: str,
    shortcode: str,
    source: DiscoveredSource,
    max_items: int | None,
) -> list[RawComment]:
    """Extract Instagram reel/video comments using yt-dlp as a fallback."""
    if not _YT_DLP_AVAILABLE:
        return []

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "getcomments": True,
        "extract_flat": False,
        "socket_timeout": max(settings.request_timeout, 15),
    }
    if max_items is not None:
        opts["extractor_args"] = {"instagram": {"max_comments": [str(max_items)]}}

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        for item in (info or {}).get("comments") or []:
            if max_items is not None and len(comments) >= max_items:
                break
            _append_unique_comment(
                comments=comments,
                seen_keys=seen_keys,
                comment_pk=item.get("id"),
                author=str(item.get("author") or "Instagram User"),
                text=str(item.get("text") or ""),
                created_at=item.get("timestamp") or item.get("time_text"),
                shortcode=shortcode,
                source=source,
            )
    except Exception as e:
        logger.debug(f"Instagram yt-dlp fallback failed for {url}: {e}")
    return comments


async def collect_instagram(source: DiscoveredSource) -> tuple[list[RawComment], DiscoveredSource]:
    """
    Collect Instagram comments for a post/reel/TV URL without requiring paid API keys,
    using optional Apify or user `INSTAGRAM_SESSION_ID` cookie for full pagination.
    """
    shortcode = _extract_shortcode(source.url)
    max_items: int | None = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    auth_mode = 2 if settings.has_apify else (1 if settings.has_instagram_session else 0)
    cache_id = f"ig_v3::{shortcode or source.url}::{auth_mode}::{settings.max_comments_per_url}"

    cache = get_cache()
    cached = cache.get("s3_instagram", cache_id)
    if cached is not None and isinstance(cached, dict):
        cached_status = cached.get("scrape_status", "success")
        if not (max_items is None and cached_status == "partial" and auth_mode > 0):
            restored = [
                RawComment(**{**item, "is_target": source.is_target})
                for item in cached.get("comments", [])
            ]
            updated = source.model_copy(
                update={
                    "comments_collected": len(restored),
                    "scrape_status": cached_status,
                    "collector_used": cached.get("collector_used", "instagram_cache"),
                    "error_message": cached.get("error_message", ""),
                }
            )
            return restored, updated

    # Layer 1: Optional Apify cloud scraper
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
                "s3_instagram",
                cache_id,
                {
                    "comments": [c.model_dump() for c in apify_comments],
                    "scrape_status": final_source.scrape_status,
                    "collector_used": final_source.collector_used,
                    "error_message": final_source.error_message,
                },
            )
            return apify_comments, final_source

    if not shortcode:
        return [], source.model_copy(
            update={
                "scrape_status": "skipped",
                "collector_used": "instagram_public_html",
                "error_message": "Skipped: Instagram URL does not reference a post, reel, or TV item",
            }
        )

    comments: list[RawComment] = []
    total_reported = 0
    collector_name = "instagram_public_html"
    session_rejected = False
    login_walled = False

    # Layer 2: Optional authenticated session cookie pagination
    session_id = _normalize_session_id(settings.instagram_session_id)
    if session_id:
        res = await asyncio.to_thread(
            _collect_via_session_api_sync, source, shortcode, session_id, max_items
        )
        comments = res["comments"]
        total_reported = res["total_reported"]
        session_rejected = res["session_rejected"]
        collector_name = "instagram_session_api"

    # Layer 3: Zero-key public HTML embedded JSON extraction
    if not comments:
        html, _final_url, _csrf = await asyncio.to_thread(
            _fetch_post_html_sync, source.url, shortcode, ""
        )
        if html:
            parsed = _parse_public_html_data(html, shortcode, source)
            comments = parsed["comments"]
            total_reported = max(total_reported, parsed["total_reported"])
            login_walled = parsed["login_walled"]
            collector_name = "instagram_public_html"

    # Layer 4: yt-dlp fallback for reels/videos
    if not comments:
        yt_comments = await asyncio.to_thread(
            _collect_via_ytdlp_ig_sync, source.url, shortcode, source, max_items
        )
        if yt_comments:
            comments = yt_comments
            collector_name = "instagram_ytdlp"

    if max_items is not None and len(comments) > max_items:
        comments = comments[:max_items]

    if not comments:
        if session_rejected:
            msg = "Failed: Instagram session cookie expired or rejected; refresh INSTAGRAM_SESSION_ID or add APIFY_API_KEY"
        elif login_walled:
            msg = "Blocked: Instagram requires login to view comments on this post; add INSTAGRAM_SESSION_ID or APIFY_API_KEY"
        else:
            msg = "No public comments found on Instagram post (add INSTAGRAM_SESSION_ID or APIFY_API_KEY for full access)"
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
        if session_rejected:
            err = (
                f"Collected {len(comments)} of ~{total_reported:,} comments "
                "(Instagram session cookie rejected/expired; refresh INSTAGRAM_SESSION_ID)"
            )
        elif not settings.has_instagram_session and not settings.has_apify:
            err = (
                f"Collected {len(comments)} of ~{total_reported:,} comments "
                "(Instagram limits logged-out pages; add INSTAGRAM_SESSION_ID cookie or APIFY_API_KEY in Optional Settings for full pagination)"
            )
        else:
            err = f"Collected {len(comments)} of ~{total_reported:,} comments"
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
        "s3_instagram",
        cache_id,
        {
            "comments": [c.model_dump() for c in comments],
            "scrape_status": updated_source.scrape_status,
            "collector_used": updated_source.collector_used,
            "error_message": updated_source.error_message,
        },
    )
    return comments, updated_source
