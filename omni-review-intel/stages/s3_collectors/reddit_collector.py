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
    cache_key = f"reddit_v3::{source.url}::{settings.max_comments_per_url}"
    cached = cache.get("s3_reddit", cache_key)
    if cached:
        comments = [RawComment.model_validate(c) for c in cached][:limit]
        for c in comments:
            c.source_url = source.url
            c.is_target = source.is_target
        source.scrape_status = "success"
        source.collector_used = "reddit (cached)"
        source.comments_collected = len(comments)
        return comments, source

    if not settings.has_reddit:
        comments, source = await _collect_via_public_json(source)
        if comments:
            cache.set("s3_reddit", cache_key, [c.model_dump() for c in comments])
            return comments, source
        logger.warning("Reddit API credentials not configured & public JSON/HTML blocked or empty — skipping")
        source.scrape_status = "skipped"
        if not source.error_message:
            source.error_message = "REDDIT_CLIENT_ID/SECRET not configured"
        return [], source

    submission_id = _extract_submission_id(source.url)

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
        comments = await asyncio.to_thread(_fetch)
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
    blocked_note = ""
    more_things_extra: list[dict] = []
    limit = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None

    def _collect_more_ids(children: list, out_ids: list[str]) -> None:
        for ch in children or []:
            if not isinstance(ch, dict):
                continue
            if ch.get("kind") == "more":
                for mid in (ch.get("data") or {}).get("children") or []:
                    if isinstance(mid, str) and mid:
                        out_ids.append(mid)
            elif ch.get("kind") == "t1":
                rep = (ch.get("data") or {}).get("replies")
                if isinstance(rep, dict):
                    _collect_more_ids((rep.get("data") or {}).get("children") or [], out_ids)

    def _fetch_chrome():
        nonlocal blocked_note
        sess = create_chrome_session(timeout=15)
        if sess is None:
            return None
        try:
            r = sess.get(clean_url, headers={"Accept": "application/json"})
            if r.status_code != 200:
                if r.status_code in (401, 403, 429):
                    blocked_note = f"Reddit public JSON HTTP {r.status_code} (add REDDIT_CLIENT_ID/SECRET)"
                return None
            payload = r.json()
            # Expand any hidden "kind": "more" comment batches via Reddit's public morechildren endpoint
            if isinstance(payload, list) and len(payload) > 1:
                link_fullname = ""
                try:
                    post_children = (payload[0].get("data") or {}).get("children") or []
                    if post_children:
                        link_fullname = str((post_children[0].get("data") or {}).get("name") or "")
                except Exception:
                    link_fullname = ""
                if not link_fullname and submission_id:
                    link_fullname = f"t3_{submission_id}"

                pending_ids: list[str] = []
                _collect_more_ids((payload[1].get("data") or {}).get("children") or [], pending_ids)
                seen_more_ids: set[str] = set()
                max_batches = 25 if limit is None else max(2, (limit // 80) + 1)
                batches_done = 0
                while pending_ids and link_fullname and batches_done < max_batches:
                    batch: list[str] = []
                    while pending_ids and len(batch) < 100:
                        mid = pending_ids.pop(0)
                        if mid not in seen_more_ids:
                            seen_more_ids.add(mid)
                            batch.append(mid)
                    if not batch:
                        break
                    batches_done += 1
                    mc_url = (
                        "https://www.reddit.com/api/morechildren.json"
                        f"?api_type=json&link_id={link_fullname}&children={','.join(batch)}&raw_json=1"
                    )
                    try:
                        mr = sess.get(mc_url, headers={"Accept": "application/json"})
                        if mr.status_code != 200:
                            break
                        mj = mr.json()
                        things = ((mj.get("json") or {}).get("data") or {}).get("things") or []
                        for th in things:
                            if not isinstance(th, dict):
                                continue
                            if th.get("kind") == "t1":
                                more_things_extra.append(th)
                            elif th.get("kind") == "more":
                                for nested_id in (th.get("data") or {}).get("children") or []:
                                    if isinstance(nested_id, str) and nested_id not in seen_more_ids:
                                        pending_ids.append(nested_id)
                    except Exception:
                        break
            return payload
        except Exception:
            return None
        finally:
            try:
                sess.close()
            except Exception:
                pass
        return None

    try:
        data = await asyncio.to_thread(_fetch_chrome)
        if data is None:
            try:
                connector = aiohttp.TCPConnector()
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"}
                async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
                    async with sess.get(clean_url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                        elif resp.status in (401, 403, 429) and not blocked_note:
                            blocked_note = f"Reddit public JSON HTTP {resp.status} (add REDDIT_CLIENT_ID/SECRET)"
            except Exception as aio_err:
                logger.debug(f"Reddit aiohttp JSON fallback failed for {clean_url}: {aio_err}")

        comments: list[RawComment] = []
        seen_keys: set[str] = set()

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
        if more_things_extra and (limit is None or len(comments) < limit):
            _walk_reddit_children(more_things_extra)

        if comments:
            source.scrape_status = "success"
            source.collector_used = "reddit_public_json"
            source.comments_collected = len(comments)
            return comments, source

        # HTML fallback: Reddit's server-rendered <shreddit-comment> elements when .json returns 403
        def _fetch_html() -> str:
            nonlocal blocked_note
            sess = create_chrome_session(timeout=15)
            if sess is None:
                return ""
            try:
                r = sess.get(
                    base_clean,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                txt = str(r.text or "") if r.status_code == 200 else ""
                if not txt or "reason=lor2" in txt or "Welcome to Reddit" in txt:
                    if not blocked_note:
                        blocked_note = "Reddit blocked anonymous access from this IP (add REDDIT_CLIENT_ID/SECRET)"
                return txt
            except Exception:
                return ""
            finally:
                try:
                    sess.close()
                except Exception:
                    pass

        html_text = await asyncio.to_thread(_fetch_html)
        if html_text and "shreddit-comment" in html_text:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_text, "html.parser")
            for idx, node in enumerate(soup.select("shreddit-comment")):
                if limit is not None and len(comments) >= limit:
                    break
                body_el = (
                    node.select_one('[slot="comment"]')
                    or node.select_one('div[id$="-comment-rtjson-content"]')
                    or node.select_one(".md")
                )
                body = body_el.get_text(" ", strip=True) if body_el else ""
                if not body or body in ("[deleted]", "[removed]"):
                    continue
                ts_el = node.select_one("faceplate-timeago, time")
                pub = str(ts_el.get("ts") or ts_el.get("datetime") or "") if ts_el else ""
                append_unique_comment(
                    comments=comments,
                    seen_keys=seen_keys,
                    comment_id=str(node.get("thingid") or f"red_html_{submission_id}_{idx}"),
                    author=str(node.get("author") or "RedditUser"),
                    text=body,
                    published_date=pub,
                    rating=None,
                    platform="Reddit",
                    source=source,
                )
            if comments:
                source.scrape_status = "success"
                source.collector_used = "reddit_public_html"
                source.comments_collected = len(comments)
                return comments, source
    except Exception as e:
        logger.debug(f"Public Reddit fetch failed for {source.url}: {e}")
    if blocked_note and not source.error_message:
        source.error_message = blocked_note
    return [], source
