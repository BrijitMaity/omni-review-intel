"""
stages/s3_collectors/youtube_collector.py — YouTube comments via Data API v3.
Falls back to yt-dlp comment extraction if no API key is configured.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import time
from typing import Any

from loguru import logger

from config import (
    effective_max_comments,
    get_cancel_event,
    get_container_memory_limit_mb,
    get_progress_cb,
    get_run_store,
    settings,
)
from core.run_store import CompactIdSet
from core.schemas import DiscoveredSource, RawComment
from core.cache import get_cache

import re

_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|shorts/|embed/|live/|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)


def _tok_key(token: str) -> bytes:
    """Compact 12-byte digest for large continuation-token sets (saves ~95% RAM)."""
    return hashlib.blake2b(token.encode("utf-8", errors="ignore"), digest_size=12).digest()


def _extract_video_id(url: str) -> str | None:
    # 1. Standard watch?v= or shorts or youtu.be
    m = _YT_ID_RE.search(url)
    if m:
        return m.group(1)
    # 2. Query param &v=...
    m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    # 3. Radio/Mix playlist list=RD<11-char-id>
    m = re.search(r"[?&]list=RD([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    # 4. General playlist: try extracting first video
    if "list=" in url:
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True, "no_warnings": True, "skip_download": True, "nocheckcertificate": True}) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = (info or {}).get("entries") or []
                if entries and entries[0].get("id"):
                    return str(entries[0]["id"])
        except Exception:
            pass
    return None


def _merge_unique_yt_comments(
    base: list[RawComment],
    extra: list[RawComment],
    max_items: int | None,
) -> list[RawComment]:
    """Merge extra YouTube comments into base list without ID or (author, text) duplicates."""
    seen_ids: set[str] = {c.comment_id for c in base if c.comment_id}
    seen_texts: set[tuple[str, str]] = {
        ((c.author or "").strip().lower(), (c.original_text or "").strip().lower())
        for c in base
    }
    for item in extra:
        if max_items is not None and len(base) >= max_items:
            break
        key = ((item.author or "").strip().lower(), (item.original_text or "").strip().lower())
        if (item.comment_id and item.comment_id in seen_ids) or key in seen_texts:
            continue
        if item.comment_id:
            seen_ids.add(item.comment_id)
        seen_texts.add(key)
        base.append(item)
    return base


async def collect_youtube(
    source: DiscoveredSource,
) -> tuple[list[RawComment], DiscoveredSource]:
    """Fetch YouTube comments for the given source URL without premature caps."""
    video_id = _extract_video_id(source.url)
    if not video_id:
        source.scrape_status = "failed"
        source.error_message = "Could not extract YouTube video ID"
        return [], source

    req_max = effective_max_comments(is_target=source.is_target)
    max_items: int | None = req_max if req_max > 0 else None

    cache = get_cache()
    cache_key = f"yt_v5::{video_id}::{req_max}"
    cached = cache.get("s3_youtube", cache_key)
    if cached:
        if isinstance(cached, dict):
            cached_items = cached.get("comments", [])
            cached_expected = int(cached.get("expected_total") or 0)
            cached_status = str(cached.get("scrape_status") or "success")
        else:
            cached_items = cached
            cached_expected = 0
            cached_status = "success"

        cache_ok = False
        if cached_items:
            if max_items is not None:
                cache_ok = len(cached_items) >= max_items or (
                    cached_status == "success" and (cached_expected == 0 or len(cached_items) >= cached_expected)
                )
            else:
                # Never get permanently stuck on an incomplete/partial cache when uncapped
                cache_ok = cached_status != "partial" and (
                    cached_expected <= 200 or len(cached_items) >= int(cached_expected * 0.85)
                )

        if cache_ok:
            comments = [RawComment.model_validate(c) for c in cached_items[:max_items]]
            for c in comments:
                c.source_url = source.url
                c.is_target = source.is_target
            source.scrape_status = cached_status if cached_status in ("success", "partial") else "success"
            source.collector_used = "cached"
            source.comments_collected = len(comments)
            return comments, source

    comments: list[RawComment] = []
    est_total = cached_expected if isinstance(cached, dict) else 0
    spooled_total = 0
    stop_reason = "completed"

    if settings.has_youtube:
        comments, source = await _collect_via_api(video_id, source)

    if not comments:
        meta_out: dict[str, Any] = {}
        comments, source = await _collect_via_innertube_curl(video_id, source, meta_out=meta_out)
        est_total = max(est_total, int(meta_out.get("est_total") or 0))
        spooled_total = int(meta_out.get("spooled_total") or 0)
        stop_reason = str(meta_out.get("stop_reason") or "completed")

    total_collected = spooled_total if spooled_total > 0 else len(comments)

    # Escalate and merge yt-dlp/downloader only when InnerTube got very few comments (<2,000)
    target_expected = min(max_items or est_total, est_total) if est_total > 0 else 0
    needs_more = total_collected < 2000 and (
        total_collected == 0 or (est_total > 200 and total_collected < int(target_expected * 0.90))
    )
    if needs_more:
        dl_source = source.model_copy()
        dl_comments, dl_source = await _collect_via_downloader(video_id, dl_source)
        if dl_comments:
            before_n = len(comments)
            comments = _merge_unique_yt_comments(comments, dl_comments, max_items)
            if len(comments) > before_n and not source.collector_used:
                source.collector_used = dl_source.collector_used

    total_collected = spooled_total if spooled_total > 0 else len(comments)
    needs_more_after_dl = total_collected < 2000 and (
        total_collected == 0 or (est_total > 200 and total_collected < int(target_expected * 0.80))
    )
    if needs_more_after_dl:
        yt_source = source.model_copy()
        yt_comments, yt_source = await _collect_via_ytdlp(video_id, yt_source)
        if yt_comments:
            before_n = len(comments)
            comments = _merge_unique_yt_comments(comments, yt_comments, max_items)
            if len(comments) > before_n:
                if before_n == 0:
                    source.collector_used = yt_source.collector_used
                else:
                    source.collector_used = f"{source.collector_used}+yt_dlp"

    if spooled_total == 0 and max_items is not None and len(comments) > max_items:
        comments = comments[:max_items]

    total_collected = spooled_total if spooled_total > 0 else len(comments)

    if total_collected == 0:
        logger.warning(f"All YouTube comment extractors returned 0 comments for {source.url}")
        source.scrape_status = "partial" if source.scrape_status == "pending" else source.scrape_status
        if not source.error_message:
            source.error_message = "No public comments found or comments disabled"
    else:
        hit_user_cap = max_items is not None and total_collected >= max_items
        if stop_reason == "cancelled":
            source.scrape_status = "partial"
            source.error_message = f"Cancelled after collecting {total_collected:,} comments"
        elif stop_reason == "rate_limited" and est_total > 200 and total_collected < int(est_total * 0.70):
            source.scrape_status = "partial"
            source.error_message = (
                f"Collected {total_collected:,} of ~{est_total:,} reported comments "
                "before hitting YouTube rate limits"
            )
        elif not hit_user_cap and est_total > 200 and total_collected < int(est_total * 0.70):
            source.scrape_status = "partial"
            source.error_message = (
                f"Collected {total_collected:,} public comments of ~{est_total:,} YouTube total "
                "(remaining count includes deleted/hidden/unreturned comments)"
            )
        else:
            source.scrape_status = "success"
            source.error_message = ""
        source.comments_collected = total_collected
        if spooled_total == 0 and len(comments) <= 15000:
            cache.set(
                "s3_youtube",
                cache_key,
                {
                    "comments": [c.model_dump() for c in comments],
                    "expected_total": est_total,
                    "scrape_status": source.scrape_status,
                    "collector_used": source.collector_used,
                },
            )
    return comments, source


def _parse_yt_like_count(raw_val) -> int:
    """Parse integer or compact YouTube like count strings like '1.2K' or '3M'."""
    if raw_val is None:
        return 0
    s = str(raw_val).strip().upper().replace(",", "")
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    try:
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
    except ValueError:
        pass
    return 0


def _search_dict(partial, key: str):
    """Recursively yield values matching `key` inside nested dicts/lists."""
    if isinstance(partial, dict):
        for k, v in partial.items():
            if k == key:
                yield v
            else:
                yield from _search_dict(v, key)
    elif isinstance(partial, list):
        for item in partial:
            yield from _search_dict(item, key)


def _extract_est_total(data: dict) -> int:
    """Extract YouTube's reported total comment count (e.g. '56K', '52,542') if available."""
    if not isinstance(data, dict):
        return 0
    best = 0
    for header in _search_dict(data, "commentsHeaderRenderer"):
        for key in ("commentsCount", "countText"):
            obj = header.get(key)
            if isinstance(obj, dict):
                txt = obj.get("simpleText") or "".join(
                    r.get("text", "") for r in (obj.get("runs") or []) if isinstance(r, dict)
                )
                for tok in re.findall(r"[\d,.]+\s*[KMkm]?", txt or ""):
                    best = max(best, _parse_yt_like_count(tok))
    for panel in _search_dict(data, "engagementPanelSectionListRenderer"):
        if (
            panel.get("panelIdentifier") == "engagement-panel-comments-section"
            or panel.get("targetId") == "comments-section"
        ):
            for hdr in _search_dict(panel, "engagementPanelTitleHeaderRenderer"):
                ctx_info = hdr.get("contextualInfo")
                if isinstance(ctx_info, dict):
                    txt = ctx_info.get("simpleText") or "".join(
                        r.get("text", "") for r in (ctx_info.get("runs") or []) if isinstance(r, dict)
                    )
                    for tok in re.findall(r"[\d,.]+\s*[KMkm]?", txt or ""):
                        best = max(best, _parse_yt_like_count(tok))
    return best


def _extract_sort_tokens(data: dict) -> tuple[str | None, str | None]:
    """Return (top_token, newest_token) from commentsHeaderRenderer or comments engagement panel sortMenu."""
    import urllib.parse

    top_tok = None
    newest_tok = None

    def _consume_menu_items(items: list[dict]) -> None:
        nonlocal top_tok, newest_tok
        for idx, item in enumerate(items or []):
            tok = (
                item.get("serviceEndpoint", {})
                .get("continuationCommand", {})
                .get("token")
            )
            if not tok:
                continue
            decoded = urllib.parse.unquote(tok)
            title = (item.get("title") or "").lower()
            if "newest" in title or (idx == 1 and not newest_tok):
                newest_tok = decoded
            elif "top" in title or (idx == 0 and not top_tok):
                top_tok = decoded

    for header in _search_dict(data, "commentsHeaderRenderer"):
        items = (
            header.get("sortMenu", {})
            .get("sortFilterSubMenuRenderer", {})
            .get("subMenuItems", [])
        )
        _consume_menu_items(items)

    if not (top_tok and newest_tok):
        for panel in _search_dict(data, "engagementPanelSectionListRenderer"):
            if (
                panel.get("panelIdentifier") == "engagement-panel-comments-section"
                or panel.get("targetId") == "comments-section"
            ):
                for menu in _search_dict(panel, "sortFilterSubMenuRenderer"):
                    _consume_menu_items(menu.get("subMenuItems", []))

    return top_tok, newest_tok


def _extract_innertube_page(
    data: dict,
    video_id: str,
    source_url: str,
    is_target: bool,
    seen_ids: set[str],
    max_items: int,
    current_count: int,
    visited_tokens: set[str],
    seen_reply_tokens: set[str],
) -> tuple[list[RawComment], int, str | None, list[str]]:
    """Extract unique comments, raw payload count, next continuation token, and reply tokens from one InnerTube page."""
    import urllib.parse

    page_comments: list[RawComment] = []
    raw_in_page = 0
    new_reply_tokens: list[str] = []

    def _can_add_more() -> bool:
        return (current_count + len(page_comments)) < max_items

    # Method 1: Modern YouTube InnerTube frameworkUpdates -> commentEntityPayload
    mutations = (
        data.get("frameworkUpdates", {})
        .get("entityBatchUpdate", {})
        .get("mutations", [])
    )
    for m in mutations:
        cep = m.get("payload", {}).get("commentEntityPayload")
        if not cep:
            continue
        props = cep.get("properties", {})
        content = (props.get("content", {}).get("content") or "").strip()
        if not content:
            continue
        raw_in_page += 1
        cid = (
            cep.get("commentId")
            or props.get("commentId")
            or m.get("entityKey")
            or f"yt_it_{video_id}_{current_count + len(page_comments)}"
        )
        if cid in seen_ids or not _can_add_more():
            continue
        seen_ids.add(cid)
        author = cep.get("author", {}).get("displayName") or "YouTubeUser"
        pub_time = props.get("publishedTime") or ""
        toolbar = cep.get("toolbar", {})
        like_cnt = _parse_yt_like_count(
            toolbar.get("likeCountNotliked") or toolbar.get("likeCount")
        )
        page_comments.append(
            RawComment(
                comment_id=cid,
                source_url=source_url,
                platform="YouTube",
                author=author,
                published_date=pub_time,
                rating=None,
                original_text=content,
                like_count=like_cnt,
                is_target=is_target,
            )
        )

    # Method 2: Legacy commentThreadRenderer, standalone commentRenderer (replies), and reply tokens
    def _parse_comment_renderer(cr: dict) -> None:
        nonlocal raw_in_page
        runs = cr.get("contentText", {}).get("runs", [])
        content = "".join(r.get("text", "") for r in runs).strip()
        if not content:
            return
        raw_in_page += 1
        cid = cr.get("commentId") or f"yt_cr_{video_id}_{current_count + len(page_comments)}"
        if cid in seen_ids or not _can_add_more():
            return
        seen_ids.add(cid)
        author = cr.get("authorText", {}).get("simpleText") or "YouTubeUser"
        pub_runs = cr.get("publishedTimeText", {}).get("runs") or [{}]
        pub_time = pub_runs[0].get("text", "")
        like_cnt = _parse_yt_like_count(cr.get("voteCount", {}).get("simpleText"))
        page_comments.append(
            RawComment(
                comment_id=cid,
                source_url=source_url,
                platform="YouTube",
                author=author,
                published_date=pub_time,
                rating=None,
                original_text=content,
                like_count=like_cnt,
                is_target=is_target,
            )
        )

    next_token = None
    for ep in data.get("onResponseReceivedEndpoints", []):
        cmd = (
            ep.get("appendContinuationItemsAction")
            or ep.get("reloadContinuationItemsCommand")
        )
        if not cmd:
            continue
        for it in cmd.get("continuationItems", []):
            ctr = it.get("commentThreadRenderer")
            if ctr:
                cr = ctr.get("comment", {}).get("commentRenderer")
                if cr:
                    _parse_comment_renderer(cr)
                # Collect reply thread continuation tokens for Phase 3 fallback
                replies = ctr.get("replies")
                if replies:
                    for cc in _search_dict(replies, "continuationCommand"):
                        rtok = cc.get("token") if isinstance(cc, dict) else None
                        if rtok:
                            r_decoded = urllib.parse.unquote(rtok)
                            r_key = _tok_key(r_decoded)
                            if r_key not in seen_reply_tokens and r_decoded not in seen_reply_tokens:
                                seen_reply_tokens.add(r_key)
                                new_reply_tokens.append(r_decoded)
            elif it.get("commentRenderer"):
                _parse_comment_renderer(it["commentRenderer"])

            cir = it.get("continuationItemRenderer")
            if cir and not next_token:
                tok = (
                    cir.get("continuationEndpoint", {})
                    .get("continuationCommand", {})
                    .get("token")
                )
                if not tok:
                    tok = (
                        cir.get("button", {})
                        .get("buttonRenderer", {})
                        .get("command", {})
                        .get("continuationCommand", {})
                        .get("token")
                    )
                if not tok:
                    for cc in _search_dict(cir, "continuationCommand"):
                        if isinstance(cc, dict) and cc.get("token"):
                            tok = cc["token"]
                            break
                if tok:
                    decoded = urllib.parse.unquote(tok)
                    d_key = _tok_key(decoded)
                    if d_key not in visited_tokens and decoded not in visited_tokens:
                        next_token = decoded

    return page_comments, raw_in_page, next_token, new_reply_tokens


async def _collect_via_innertube_curl(
    video_id: str,
    source: DiscoveredSource,
    meta_out: dict[str, Any] | None = None,
) -> tuple[list[RawComment], DiscoveredSource]:
    """Scrape YouTube comments directly using YouTube's InnerTube API with multi-transport fallback.

    Tries Watch HTML first (`curl_cffi` + `httpx`), and automatically bypasses HTTP 429 /
    bot challenges on `/watch` via `/embed/{video_id}` + direct `POST /youtubei/v1/next`,
    collecting Top -> Newest -> Replies up to `settings.max_comments_per_url`.
    """
    import json
    import re
    import urllib.parse

    cancel_ev = get_cancel_event()
    run_store = get_run_store()
    progress_cb = get_progress_cb()
    try:
        event_loop = asyncio.get_running_loop()
    except RuntimeError:
        event_loop = None

    try:
        from curl_cffi import CurlOpt, requests as c_requests
        _curl_available = True
        _curl_opts = {CurlOpt.IPRESOLVE: 1}
    except ImportError:
        try:
            from curl_cffi import requests as c_requests
            _curl_available = True
            _curl_opts = {}
        except ImportError:
            c_requests = None
            _curl_available = False
            _curl_opts = {}

    try:
        import httpx
        _httpx_available = True
    except ImportError:
        httpx = None
        _httpx_available = False

    if not _curl_available and not _httpx_available:
        logger.debug("Neither curl_cffi nor httpx is installed, skipping innertube collector")
        return [], source

    profiles: list[tuple[str, str]] = []
    if _curl_available:
        profiles.append(("curl", "chrome120"))
    if _httpx_available:
        profiles.append(("httpx", "chrome131"))
    if _curl_available:
        profiles.extend([("curl", "chrome124"), ("curl", "chrome110"), ("curl", "edge101")])

    _BROWSER_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    last_error_note = ""

    def _new_session(profile_idx: int = 0) -> tuple[Any, int]:
        idx = profile_idx % len(profiles)
        kind, variant = profiles[idx]
        if kind == "curl" and c_requests is not None:
            s = c_requests.Session(impersonate=variant, curl_options=_curl_opts)
            s.cookies.set("CONSENT", "YES+cb", domain=".youtube.com")
            s.cookies.set("SOCS", "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg", domain=".youtube.com")
            return s, idx
        s = httpx.Client(timeout=20, follow_redirects=True, headers=_BROWSER_HEADERS)
        s.cookies.set("CONSENT", "YES+cb", domain=".youtube.com")
        s.cookies.set("SOCS", "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg", domain=".youtube.com")
        return s, idx

    def _close_quietly(sess: Any) -> None:
        if sess is None:
            return
        try:
            sess.close()
        except Exception:
            pass

    def _extract_watch_init(html_text: str) -> tuple[str | None, str, dict | None, str]:
        m_key_w = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html_text)
        w_key = m_key_w.group(1) if m_key_w else None
        m_ver_w = re.search(r'"(?:INNERTUBE_CLIENT_VERSION|clientVersion)"\s*:\s*"([^"]+)"', html_text)
        w_ver = m_ver_w.group(1) if m_ver_w else "2.20261125.00.00"
        m_vis_w = re.search(r'"VISITOR_DATA"\s*:\s*"([^"]+)"', html_text)
        w_vis = m_vis_w.group(1) if m_vis_w else ""
        data_obj = None
        m_init = re.search(r"var ytInitialData\s*=\s*({.+?});</script>", html_text) or re.search(
            r"ytInitialData\s*=\s*({.+?});", html_text
        )
        if m_init:
            try:
                data_obj = json.loads(m_init.group(1))
            except Exception:
                data_obj = None
        if data_obj is None:
            marker = re.search(r"ytInitialData\s*=\s*\{", html_text)
            if marker:
                try:
                    start_idx = html_text.index("{", marker.start())
                    data_obj, _ = json.JSONDecoder().raw_decode(html_text, start_idx)
                except Exception:
                    data_obj = None
        if not w_vis and isinstance(data_obj, dict):
            w_vis = str((data_obj.get("responseContext") or {}).get("visitorData") or "")
        return w_key, w_ver, data_obj, w_vis

    def _extract_comment_bootstrap(data_obj: dict, allow_generic_fallback: bool = False) -> tuple[str | None, str | None]:
        tok = None
        for sec in _search_dict(data_obj, "itemSectionRenderer"):
            if (
                sec.get("sectionIdentifier") == "comment-item-section"
                or sec.get("targetId") == "comments-section"
            ):
                for raw_tok in _search_dict(sec, "token"):
                    tok = urllib.parse.unquote(raw_tok)
                    break
                if tok:
                    break
        if not tok:
            for panel in _search_dict(data_obj, "engagementPanelSectionListRenderer"):
                if (
                    panel.get("panelIdentifier") == "engagement-panel-comments-section"
                    or panel.get("targetId") == "comments-section"
                ):
                    for raw_tok in _search_dict(panel, "token"):
                        tok = urllib.parse.unquote(raw_tok)
                        break
                    if tok:
                        break
        if not tok and allow_generic_fallback:
            for cand in _search_dict(data_obj, "continuationItemRenderer"):
                for raw_tok in _search_dict(cand, "token"):
                    tok = urllib.parse.unquote(raw_tok)
                    break
                if tok:
                    break
        top_t, newest_t = _extract_sort_tokens(data_obj)
        return tok or top_t, newest_t

    def _fetch() -> list[RawComment]:
        nonlocal last_error_note
        active_profile = 0
        session, active_profile = _new_session(active_profile)

        api_key: str | None = None
        client_version = "2.20261125.00.00"
        visitor_data = ""
        est_total = 0
        init_data: dict | None = None
        initial_token: str | None = None
        newest_token: str | None = None

        # Step 1: Try standard /watch and mobile m.youtube.com across profiles (curl_cffi -> httpx)
        watch_urls = [
            f"https://www.youtube.com/watch?v={video_id}",
            f"https://m.youtube.com/watch?v={video_id}",
        ]
        for attempt_idx, target_watch_url in enumerate(watch_urls):
            try:
                r = session.get(target_watch_url, timeout=15)
                final_url = str(getattr(r, "url", ""))
                if r.status_code == 200 and r.text and "sorry/index" not in final_url:
                    low_html = r.text.lower()
                    if "confirm you\u2019re not a bot" in low_html or "confirm you're not a bot" in low_html or "sign in to confirm" in low_html:
                        last_error_note = "YouTube bot-check rate-limited this server IP (add YOUTUBE_API_KEY in Settings/Secrets for cloud access)"
                    w_key, w_ver, w_data, w_vis = _extract_watch_init(r.text)
                    if w_data:
                        tok, nt = _extract_comment_bootstrap(w_data, allow_generic_fallback=True)
                        if tok:
                            api_key, client_version, init_data = w_key, w_ver, w_data
                            visitor_data = w_vis or visitor_data
                            est_total = max(est_total, _extract_est_total(w_data))
                            initial_token, newest_token = tok, nt
                            last_error_note = ""
                            break
                elif r.status_code in (403, 429) or "sorry/index" in final_url:
                    last_error_note = f"YouTube /watch HTTP {r.status_code} rate-limited"
            except Exception as e:
                logger.debug(f"InnerTube GET watch attempt {attempt_idx + 1} failed: {e}")

            if attempt_idx + 1 < len(watch_urls):
                _close_quietly(session)
                session, active_profile = _new_session(active_profile + 1)

        # Step 2: Bypass /watch 429 or bot gate using /embed + direct POST /youtubei/v1/next
        if not initial_token:
            for e_try in range(min(3, len(profiles))):
                try:
                    r_emb = session.get(f"https://www.youtube.com/embed/{video_id}", timeout=15)
                    if r_emb.status_code == 200 and r_emb.text:
                        m_ek = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', r_emb.text)
                        m_ev = re.search(r'"(?:INNERTUBE_CLIENT_VERSION|clientVersion)"\s*:\s*"([^"]+)"', r_emb.text)
                        m_evis = re.search(r'"VISITOR_DATA"\s*:\s*"([^"]+)"', r_emb.text)
                        if m_ek:
                            api_key = m_ek.group(1)
                        if m_ev:
                            client_version = m_ev.group(1)
                        if m_evis and not visitor_data:
                            visitor_data = m_evis.group(1)
                        break
                except Exception as e:
                    logger.debug(f"InnerTube GET embed attempt {e_try + 1} failed: {e}")
                _close_quietly(session)
                session, active_profile = _new_session(active_profile + 1)

            next_endpoint = (
                f"https://www.youtube.com/youtubei/v1/next?key={api_key}"
                if api_key
                else "https://www.youtube.com/youtubei/v1/next"
            )
            client_ctx: dict[str, Any] = {
                "hl": "en",
                "gl": "US",
                "clientName": "WEB",
                "clientVersion": client_version,
            }
            if visitor_data:
                client_ctx["visitorData"] = visitor_data
            next_payload = {"context": {"client": client_ctx}, "videoId": video_id}
            next_headers = {
                "X-Youtube-Client-Name": "1",
                "X-Youtube-Client-Version": client_version,
                "Origin": "https://www.youtube.com",
                "Referer": f"https://www.youtube.com/watch?v={video_id}",
            }
            if visitor_data:
                next_headers["X-Goog-Visitor-Id"] = visitor_data
            for n_try in range(len(profiles)):
                try:
                    try:
                        r_next = session.post(next_endpoint, json=next_payload, headers=next_headers, timeout=20)
                    except TypeError:
                        r_next = session.post(next_endpoint, json=next_payload, timeout=20)
                    if r_next.status_code == 200:
                        next_data = r_next.json()
                        vd = (next_data.get("responseContext") or {}).get("visitorData")
                        if vd:
                            visitor_data = str(vd)
                        tok, nt = _extract_comment_bootstrap(next_data, allow_generic_fallback=False)
                        if tok:
                            init_data = next_data
                            est_total = max(est_total, _extract_est_total(next_data))
                            initial_token = tok
                            newest_token = nt
                            last_error_note = ""
                            break
                    elif not last_error_note:
                        last_error_note = f"YouTube InnerTube /next HTTP {r_next.status_code}"
                except Exception as e:
                    if not last_error_note:
                        last_error_note = f"YouTube InnerTube /next error: {e}"
                    logger.debug(f"InnerTube POST /next bootstrap attempt {n_try + 1} failed: {e}")
                _close_quietly(session)
                session, active_profile = _new_session(active_profile + 1)

        if not initial_token:
            _close_quietly(session)
            return []

        if meta_out is not None and est_total:
            meta_out["est_total"] = est_total

        req_max = effective_max_comments(is_target=source.is_target)
        max_items = req_max if req_max > 0 else 5_000_000

        import queue as queue_mod
        import threading
        from concurrent.futures import ThreadPoolExecutor

        SPOOL_THRESHOLD = 5_000
        FLUSH_BATCH = 1_000
        MWEB_VERSION = "2.20250501.01.00"

        started_at = time.monotonic()
        mem_comments: list[RawComment] = []
        flush_buf: list[RawComment] = []
        spooled_total = 0
        total_collected = 0
        rate_limited_hit = False

        seen_ids = CompactIdSet()
        visited_tokens: set[bytes | str] = set()
        seen_reply_tokens: set[bytes | str] = set()
        reply_queue: queue_mod.Queue[str] = queue_mod.Queue()

        state_lock = threading.Lock()
        data_lock = threading.Lock()
        tok_lock = threading.Lock()
        emit_lock = threading.Lock()
        throttle_until = 0.0
        last_emit_ts = 0.0

        post_url = (
            f"https://www.youtube.com/youtubei/v1/next?key={api_key}"
            if api_key
            else "https://www.youtube.com/youtubei/v1/next"
        )
        MAX_CONSECUTIVE_EMPTY = 3
        MAX_OVERLAP_EMPTY_PAGES = 45
        MAX_RETRIES_PER_PAGE = max(5, len(profiles) + 1)
        total_requests = 0

        def _cancelled() -> bool:
            return bool(cancel_ev is not None and cancel_ev.is_set())

        def _get_count() -> int:
            return total_collected

        def _emit_progress(force: bool = False) -> None:
            nonlocal last_emit_ts
            if progress_cb is None or event_loop is None:
                return
            now = time.monotonic()
            with emit_lock:
                if not force and (now - last_emit_ts) < 2.5:
                    return
                last_emit_ts = now
                cur_cnt = total_collected
                est = est_total
                dt = max(now - started_at, 0.5)
            rate = cur_cnt / dt
            if est > 0:
                msg = (
                    f"YouTube ({video_id}): {cur_cnt:,} / ~{est:,} comments collected "
                    f"({rate:,.0f}/s)…"
                )
            else:
                msg = f"YouTube ({video_id}): {cur_cnt:,} comments collected ({rate:,.0f}/s)…"
            try:
                event_loop.call_soon_threadsafe(asyncio.ensure_future, progress_cb("Stage 3", msg))
            except RuntimeError:
                pass

        def _record_comments(items: list[RawComment]) -> int:
            nonlocal spooled_total, total_collected
            if not items:
                return 0
            to_flush: list[RawComment] | None = None
            added = 0
            with data_lock:
                for it in items:
                    if total_collected >= max_items:
                        break
                    if it.comment_id in seen_ids:
                        continue
                    seen_ids.add(it.comment_id)
                    total_collected += 1
                    added += 1
                    if run_store is not None and (
                        spooled_total > 0 or (len(mem_comments) + len(flush_buf) + 1) > SPOOL_THRESHOLD
                    ):
                        if mem_comments:
                            flush_buf.extend(mem_comments)
                            mem_comments.clear()
                        flush_buf.append(it)
                        if len(flush_buf) >= FLUSH_BATCH:
                            to_flush = list(flush_buf)
                            flush_buf.clear()
                            spooled_total += len(to_flush)
                    else:
                        mem_comments.append(it)
            if to_flush and run_store is not None:
                run_store.add_raw_batch(to_flush)
            if added:
                _emit_progress(force=False)
            return added

        def _post_with_session(
            sess: Any,
            prof_idx: int,
            tok: str,
            *,
            max_attempts: int | None = None,
            req_timeout: int = 25,
            update_shared_state: bool = True,
            fixed_visitor: str = "",
            client_mode: str = "WEB",
        ) -> tuple[dict | None, Any, int]:
            nonlocal total_requests, last_error_note, visitor_data, throttle_until, rate_limited_hit
            attempts = max_attempts or MAX_RETRIES_PER_PAGE
            with state_lock:
                total_requests += 1
            cur_mode = client_mode
            for attempt in range(1, attempts + 1):
                if _cancelled():
                    return None, sess, prof_idx
                # Respect gentle shared backoff if any thread recently saw 429
                with state_lock:
                    wait_until = throttle_until
                    cur_vis = visitor_data if update_shared_state else (visitor_data or fixed_visitor)
                sleep_gap = wait_until - time.monotonic()
                if sleep_gap > 0:
                    time.sleep(min(sleep_gap, 2.5))

                use_mweb = cur_mode == "MWEB"
                c_ver = MWEB_VERSION if use_mweb else client_version
                client_obj: dict[str, Any] = {
                    "hl": "en",
                    "gl": "US",
                    "clientName": "MWEB" if use_mweb else "WEB",
                    "clientVersion": c_ver,
                }
                if cur_vis:
                    client_obj["visitorData"] = cur_vis
                payload = {
                    "context": {"client": client_obj},
                    "continuation": tok,
                }
                yt_headers = {
                    "X-Youtube-Client-Name": "2" if use_mweb else "1",
                    "X-Youtube-Client-Version": c_ver,
                    "Origin": "https://www.youtube.com",
                    "Referer": f"https://www.youtube.com/watch?v={video_id}",
                }
                if cur_vis:
                    yt_headers["X-Goog-Visitor-Id"] = cur_vis
                try:
                    try:
                        resp = sess.post(post_url, json=payload, headers=yt_headers, timeout=req_timeout)
                    except TypeError:
                        resp = sess.post(post_url, json=payload, timeout=req_timeout)
                    if resp.status_code == 200:
                        cand = resp.json()
                        if isinstance(cand, dict):
                            vd = (cand.get("responseContext") or {}).get("visitorData")
                            if vd and update_shared_state:
                                with state_lock:
                                    visitor_data = str(vd)
                            if "onResponseReceivedEndpoints" in cand or "frameworkUpdates" in cand:
                                return cand, sess, prof_idx
                        wait = min(4.0 if update_shared_state else 1.5, 0.75 * attempt)
                        if update_shared_state:
                            with state_lock:
                                last_error_note = "YouTube InnerTube returned incomplete payload (retrying)"
                        logger.debug(
                            f"YT innertube HTTP 200 incomplete payload (attempt {attempt}/{attempts}), rotating client"
                        )
                        if attempt < attempts:
                            time.sleep(wait)
                        _close_quietly(sess)
                        sess, prof_idx = _new_session(prof_idx + 1)
                    elif resp.status_code == 400 and use_mweb:
                        # Fallback to WEB immediately if a proxy/mock rejects MWEB
                        cur_mode = "WEB"
                    elif resp.status_code in (403, 429, 500, 502, 503, 504):
                        wait = min(4.0 if update_shared_state else 1.8, 0.75 * attempt)
                        with state_lock:
                            if resp.status_code in (403, 429):
                                rate_limited_hit = True
                                throttle_until = max(throttle_until, time.monotonic() + wait)
                            if update_shared_state:
                                last_error_note = f"YouTube InnerTube HTTP {resp.status_code} rate-limited"
                        logger.debug(f"YT innertube HTTP {resp.status_code}, rotating client & sleeping {wait:.1f}s")
                        if attempt < attempts:
                            time.sleep(wait)
                        _close_quietly(sess)
                        sess, prof_idx = _new_session(prof_idx + 1)
                    else:
                        logger.debug(f"YT innertube HTTP {resp.status_code}")
                        return None, sess, prof_idx
                except Exception as exc:
                    logger.debug(f"YT innertube attempt {attempt} error: {exc}")
                    _close_quietly(sess)
                    sess, prof_idx = _new_session(prof_idx + 1)
                    if attempt < attempts:
                        time.sleep(min(2.0 if update_shared_state else 1.0, 0.5 * attempt))
            return None, sess, prof_idx

        def _extract_page_locked(
            data: dict,
            remaining_budget: int,
        ) -> tuple[list[RawComment], int, str | None, list[str]]:
            local_seen: set[str] = set()
            # Keep _extract_innertube_page safe and lock-free during JSON parse, then gate global tokens
            with tok_lock:
                v_snap = visited_tokens
                r_snap = seen_reply_tokens
                page_items, raw_in_page, next_tok, new_rep = _extract_innertube_page(
                    data=data,
                    video_id=video_id,
                    source_url=source.url,
                    is_target=source.is_target,
                    seen_ids=local_seen,
                    max_items=remaining_budget,
                    current_count=0,
                    visited_tokens=v_snap,
                    seen_reply_tokens=r_snap,
                )
            return page_items, raw_in_page, next_tok, new_rep

        def _run_pagination(
            sess_in: Any,
            prof_in: int,
            start_token: str,
            phase_name: str,
            *,
            capture_sort: bool = False,
            max_pages: int | None = None,
            client_mode: str = "WEB",
        ) -> tuple[str | None, Any, int]:
            nonlocal newest_token, est_total
            sess, prof = sess_in, prof_in
            token: str | None = start_token
            consecutive_empty = 0
            consecutive_zero_new = 0
            page_num = 0

            while token and _get_count() < max_items:
                if _cancelled():
                    return None, sess, prof
                tok_k = _tok_key(token)
                with tok_lock:
                    if tok_k in visited_tokens or token in visited_tokens:
                        return None, sess, prof
                    visited_tokens.add(tok_k)
                page_num += 1

                data, sess, prof = _post_with_session(
                    sess, prof, token, client_mode=client_mode
                )
                if data is None:
                    return None, sess, prof

                if capture_sort:
                    hdr_est = _extract_est_total(data)
                    with state_lock:
                        if hdr_est > est_total:
                            est_total = hdr_est
                            if meta_out is not None:
                                meta_out["est_total"] = est_total
                        if not newest_token:
                            _, nt = _extract_sort_tokens(data)
                            if nt:
                                newest_token = nt

                rem = max(1, max_items - _get_count())
                page_items, raw_in_page, next_tok, new_rep = _extract_page_locked(data, rem)
                added = _record_comments(page_items)
                for rt in new_rep:
                    reply_queue.put(rt)

                if raw_in_page == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                        logger.debug(
                            f"YT innertube [{phase_name}] {MAX_CONSECUTIVE_EMPTY} consecutive empty pages "
                            f"at page {page_num} (total={_get_count()})"
                        )
                        return None, sess, prof
                else:
                    consecutive_empty = 0
                    if added == 0:
                        consecutive_zero_new += 1
                        if consecutive_zero_new >= MAX_OVERLAP_EMPTY_PAGES:
                            logger.info(
                                f"YT innertube [{phase_name}] converged after {consecutive_zero_new} overlap pages "
                                f"at { _get_count():,} unique comments"
                            )
                            return None, sess, prof
                    else:
                        consecutive_zero_new = 0

                if page_num % 100 == 0:
                    logger.info(
                        f"YT innertube [{phase_name}] page {page_num}: "
                        f"{_get_count():,}/{max_items:,} unique comments"
                    )

                token = next_tok
                if max_pages is not None and page_num >= max_pages:
                    return token, sess, prof

            return None, sess, prof

        # Step 1: Fetch Page 1 via WEB to capture sort tokens (newest_token) and est_total cleanly
        top_page_cap = 1 if max_items > 1000 else None
        top_resume_token, session, active_profile = _run_pagination(
            session,
            active_profile,
            initial_token,
            "Top",
            capture_sort=True,
            max_pages=top_page_cap,
            client_mode="WEB",
        )

        # Reply helper worker shared across both sequential (<=1000) and concurrent (>1000) modes
        max_sub_pages = 500 if max_items >= 1_000_000 else max(30, (max_items // 15) + 5)

        def _drain_single_reply_thread(
            rtok: str,
            ws: Any,
            w_prof: int,
            *,
            client_mode: str = "MWEB",
        ) -> tuple[Any, int]:
            pending_rtoks: list[str] = [rtok]
            local_visited: set[bytes | str] = set()
            seen_sub_reps: set[bytes | str] = set()
            sub_pages = 0
            while (
                not _cancelled()
                and _get_count() < max_items
                and pending_rtoks
                and sub_pages < max_sub_pages
            ):
                cur_tok = pending_rtoks.pop(0)
                if not cur_tok:
                    continue
                cur_k = _tok_key(cur_tok)
                with tok_lock:
                    if cur_k in visited_tokens or cur_tok in visited_tokens:
                        continue
                    visited_tokens.add(cur_k)
                local_visited.add(cur_k)
                sub_pages += 1
                data, ws, w_prof = _post_with_session(
                    ws,
                    w_prof,
                    cur_tok,
                    max_attempts=3,
                    req_timeout=15,
                    update_shared_state=False,
                    fixed_visitor=visitor_data,
                    client_mode=client_mode,
                )
                if data is None:
                    continue
                rem = max(1, max_items - _get_count())
                items, _, next_rtok, nested_reps = _extract_innertube_page(
                    data=data,
                    video_id=video_id,
                    source_url=source.url,
                    is_target=source.is_target,
                    seen_ids=set(),
                    max_items=rem,
                    current_count=0,
                    visited_tokens=local_visited,
                    seen_reply_tokens=seen_sub_reps,
                )
                _record_comments(items)
                if next_rtok:
                    nk = _tok_key(next_rtok)
                    if nk not in local_visited and next_rtok not in local_visited:
                        pending_rtoks.insert(0, next_rtok)
                for nr in nested_reps:
                    if nr:
                        nrk = _tok_key(nr)
                        if nrk not in local_visited and nr not in local_visited:
                            pending_rtoks.append(nr)
            return ws, w_prof

        # Step 2: Large / uncapped runs -> overlap Top-Continue + Newest + 4 Reply workers on MWEB!
        has_more_cursors = bool(
            (top_resume_token and _tok_key(top_resume_token) not in visited_tokens)
            or (newest_token and _tok_key(newest_token) not in visited_tokens)
        )
        if not _cancelled() and _get_count() < max_items and max_items > 1000 and has_more_cursors:
            cursors_done = threading.Event()
            cursor_tasks: list[tuple[str, str]] = []
            if top_resume_token and _tok_key(top_resume_token) not in visited_tokens:
                cursor_tasks.append((top_resume_token, "Top-Continue"))
            if (
                newest_token
                and _tok_key(newest_token) not in visited_tokens
                and newest_token not in visited_tokens
            ):
                cursor_tasks.append((newest_token, "Newest"))

            remaining_cursors = len(cursor_tasks)
            cursor_lock = threading.Lock()
            worker_sessions: list[Any] = []
            ws_lock = threading.Lock()

            def _cursor_runner(start_tok: str, p_name: str) -> None:
                nonlocal remaining_cursors
                w_sess, w_prof = _new_session(active_profile)
                with ws_lock:
                    worker_sessions.append(w_sess)
                try:
                    _, w_sess, w_prof = _run_pagination(
                        w_sess,
                        w_prof,
                        start_tok,
                        p_name,
                        capture_sort=False,
                        client_mode="MWEB",
                    )
                finally:
                    _close_quietly(w_sess)
                    with cursor_lock:
                        remaining_cursors -= 1
                        if remaining_cursors <= 0:
                            cursors_done.set()

            def _reply_runner(worker_idx: int) -> None:
                w_sess, w_prof = _new_session(active_profile + worker_idx + 1)
                with ws_lock:
                    worker_sessions.append(w_sess)
                try:
                    while not _cancelled() and _get_count() < max_items:
                        try:
                            rt = reply_queue.get(timeout=0.25)
                        except queue_mod.Empty:
                            if cursors_done.is_set() and reply_queue.empty():
                                break
                            continue
                        w_sess, w_prof = _drain_single_reply_thread(
                            rt, w_sess, w_prof, client_mode="MWEB"
                        )
                finally:
                    _close_quietly(w_sess)

            if not cursor_tasks:
                cursors_done.set()

            reply_worker_count = 2
            with ThreadPoolExecutor(max_workers=len(cursor_tasks) + reply_worker_count) as pool:
                futs = [pool.submit(_cursor_runner, t, nm) for t, nm in cursor_tasks]
                for w_i in range(reply_worker_count):
                    futs.append(pool.submit(_reply_runner, w_i))
                for fut in futs:
                    fut.result()
        else:
            # Small / capped / single-page runs: run sequential Newest/Top + reply drain cleanly
            if (
                not _cancelled()
                and _get_count() < max_items
                and newest_token
                and _tok_key(newest_token) not in visited_tokens
                and newest_token not in visited_tokens
            ):
                _, session, active_profile = _run_pagination(
                    session, active_profile, newest_token, "Newest", capture_sort=False, client_mode="WEB"
                )
            if (
                not _cancelled()
                and _get_count() < max_items
                and top_resume_token
                and _tok_key(top_resume_token) not in visited_tokens
                and (est_total == 0 or _get_count() < int(est_total * 0.98))
            ):
                _, session, active_profile = _run_pagination(
                    session, active_profile, top_resume_token, "Top-Continue", capture_sort=False, client_mode="WEB"
                )
            if not _cancelled() and _get_count() < max_items and not reply_queue.empty():
                while not _cancelled() and _get_count() < max_items:
                    try:
                        rt = reply_queue.get_nowait()
                    except queue_mod.Empty:
                        break
                    session, active_profile = _drain_single_reply_thread(
                        rt, session, active_profile, client_mode="WEB"
                    )

        # Flush any buffered spooled comments before closing
        if flush_buf and run_store is not None:
            run_store.add_raw_batch(flush_buf)
            spooled_total += len(flush_buf)
            flush_buf.clear()

        _emit_progress(force=True)
        _close_quietly(session)

        if _cancelled():
            stop_reason = "cancelled"
        elif _get_count() >= max_items:
            stop_reason = "cap_reached"
        elif rate_limited_hit and est_total > 200 and _get_count() < int(est_total * 0.70):
            stop_reason = "rate_limited"
        else:
            stop_reason = "completed"

        if meta_out is not None:
            meta_out["est_total"] = est_total
            meta_out["spooled_total"] = spooled_total
            meta_out["stop_reason"] = stop_reason

        logger.info(
            f"YT innertube finished: {total_requests} API calls, "
            f"{total_collected:,} unique comments (spooled={spooled_total:,}, est_total={est_total:,}) for video {video_id}"
        )
        return mem_comments

    try:
        comments = await asyncio.to_thread(_fetch)
        collected_n = int((meta_out or {}).get("spooled_total") or 0) or len(comments)
        if collected_n > 0:
            source.scrape_status = "success"
            source.collector_used = "youtube_innertube"
            source.comments_collected = collected_n
            logger.info(
                f"Stage 3: youtube_innertube scraped {collected_n:,} comments for {source.url}"
            )
            return comments, source
        if last_error_note and not source.error_message:
            source.error_message = last_error_note
    except Exception as e:
        logger.debug(f"youtube_innertube error for {video_id}: {e}")
        if not source.error_message:
            source.error_message = str(e)[:200]

    return [], source


async def _collect_via_downloader(
    video_id: str, source: DiscoveredSource
) -> tuple[list[RawComment], DiscoveredSource]:
    """Fetch YouTube comments via youtube-comment-downloader without requiring API keys."""
    try:
        from youtube_comment_downloader import YoutubeCommentDownloader
    except ImportError:
        return [], source

    def _call():
        d = YoutubeCommentDownloader()
        d.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        d.session.cookies.set("CONSENT", "YES+cb", domain=".youtube.com")
        d.session.cookies.set("SOCS", "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg", domain=".youtube.com")
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        req_max = effective_max_comments(is_target=source.is_target)
        max_c = req_max if req_max > 0 else None
        results: list[dict] = []
        for sort_mode in (1, 0):
            try:
                for item in d.get_comments_from_url(watch_url, sort_by=sort_mode):
                    results.append(item)
                    if max_c is not None and len(results) >= max_c:
                        return results
            except Exception as stream_err:
                logger.debug(f"youtube_downloader stream interrupted at {len(results)} items (sort={sort_mode}): {stream_err}")
                if results:
                    return results
            if results:
                return results
        return results

    try:
        raw_list = await asyncio.to_thread(_call)
        comments: list[RawComment] = []
        for i, c in enumerate(raw_list):
            text = (c.get("text") or "").strip()
            if not text:
                continue
            comments.append(
                RawComment(
                    comment_id=str(c.get("cid") or f"yt_dl_{video_id}_{i}"),
                    source_url=source.url,
                    platform="YouTube",
                    author=str(c.get("author") or "YouTubeUser"),
                    published_date=str(c.get("time") or ""),
                    rating=None,
                    original_text=text,
                    like_count=int(c.get("votes") or 0) if str(c.get("votes") or "").isdigit() else 0,
                    reply_count=0,
                    is_target=source.is_target,
                )
            )
        if comments:
            source.scrape_status = "success"
            source.collector_used = "youtube_downloader"
            source.comments_collected = len(comments)
            logger.info(f"Stage 3: youtube_downloader scraped {len(comments)} comments for {source.url}")
            return comments, source
    except Exception as e:
        logger.debug(f"youtube_downloader fallback for {video_id}: {e}")

    return [], source


async def _collect_via_ytdlp(
    video_id: str, source: DiscoveredSource
) -> tuple[list[RawComment], DiscoveredSource]:
    """Fetch YouTube comments via yt-dlp when API key is not configured."""
    try:
        import yt_dlp
    except ImportError:
        return [], source

    def _call():
        req_max = effective_max_comments(is_target=source.is_target)
        yt_args: dict[str, Any] = {
            "player_client": ["visionos", "mweb", "tv_embedded", "android", "ios", "web"],
        }
        if req_max > 0:
            yt_args["max_comments"] = [
                str(req_max),
                "all",
                "all",
                "all",
            ]
        ydl_opts: dict[str, Any] = {
            "extract_flat": False,
            "get_comments": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "socket_timeout": max(settings.request_timeout, 15),
            "extractor_args": {"youtube": yt_args},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(source.url, download=False)

    try:
        info = await asyncio.to_thread(_call)
        raw_list = (info or {}).get("comments") or []
        comments: list[RawComment] = []
        req_max = effective_max_comments(is_target=source.is_target)
        limit = req_max if req_max > 0 else None
        for i, c in enumerate(raw_list[:limit]):
            text = (c.get("text") or "").strip()
            if not text:
                continue
            comments.append(
                RawComment(
                    comment_id=str(c.get("id") or f"yt_dlp_{video_id}_{i}"),
                    source_url=source.url,
                    platform="YouTube",
                    author=str(c.get("author") or "YouTubeUser"),
                    published_date=str(c.get("timestamp") or ""),
                    rating=None,
                    original_text=text,
                    is_target=source.is_target,
                )
            )
        if comments:
            source.scrape_status = "success"
            source.collector_used = "yt_dlp_fallback"
            source.comments_collected = len(comments)
            return comments, source
    except Exception as e:
        logger.debug(f"yt-dlp comment fallback failed for {source.url}: {e}")
    return [], source


async def _collect_via_api(
    video_id: str, source: DiscoveredSource
) -> tuple[list[RawComment], DiscoveredSource]:
    """Use YouTube Data API v3 to fetch up to MAX_COMMENTS_PER_URL comments."""
    def _fetch():
        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError("google-api-python-client not installed")

        youtube = build("youtube", "v3", developerKey=settings.youtube_api_key)
        comments: list[RawComment] = []
        page_token = None
        req_max = effective_max_comments(is_target=source.is_target)
        max_items = req_max if req_max > 0 else 1000000

        while len(comments) < max_items:
            request_kwargs: dict = {
                "part": "snippet",
                "videoId": video_id,
                "maxResults": min(100, max_items - len(comments)),
                "textFormat": "plainText",
            }
            if page_token:
                request_kwargs["pageToken"] = page_token

            resp = youtube.commentThreads().list(**request_kwargs).execute()

            for item in resp.get("items", []):
                top = item["snippet"]["topLevelComment"]["snippet"]
                text = top.get("textDisplay", "").strip()
                if not text:
                    continue
                comments.append(
                    RawComment(
                        source_url=source.url,
                        platform="YouTube",
                        author=top.get("authorDisplayName", "Anonymous"),
                        published_date=top.get("publishedAt", ""),
                        rating=None,
                        original_text=text,
                        is_target=source.is_target,
                    )
                )

            page_token = resp.get("nextPageToken")
            if not page_token or len(comments) >= max_items:
                break

        return comments

    try:
        comments = await asyncio.to_thread(_fetch)
        source.scrape_status = "success"
        source.collector_used = "youtube_data_api_v3"
        source.comments_collected = len(comments)
        logger.info(f"YouTube: {len(comments)} comments from {source.url}")
        return comments, source
    except Exception as e:
        logger.error(f"YouTube API error for {source.url}: {e}")
        source.scrape_status = "failed"
        source.error_message = str(e)[:300]
        return [], source
