"""
stages/s3_collectors/web_collector.py — Universal web, social, forum, blog & review collector.

Layered extraction pipeline for Twitter/X, Trustpilot, Discourse forums, blogs, news comments,
and general e-commerce websites without requiring paid APIs:
  1. Twitter/X FxTwitter & Syndication JSON endpoints (bypasses X's JS-only block).
  2. Discourse forum JSON (`<topic_url>.json`) for multi-post technical/product forums.
  3. Multi-page Chrome TLS HTML fetch (`curl_cffi` IPv4 → `aiohttp` → `crawl4ai` Playwright)
     extracting:
       a) Schema.org JSON-LD `Review` / `Comment` / `DiscussionForumPosting` items
       b) Structured DOM review/comment selectors (WordPress, Disqus/Hyvor, Judge.me, Yotpo,
          WooCommerce, XenForo, GSMArena, review cards, `#comments`, `#reviews`)
       c) Follows `rel="next"` / pagination links up to `max_comments_per_url` (0 = uncapped)
       d) Optional LLM or high-capacity heuristic extraction on full page text (no 8k/50-item cap)
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from config import settings
from core.cache import get_cache
from core.schemas import DiscoveredSource, RawComment
from stages.s3_collectors._http_utils import (
    append_unique_comment,
    clamp_rating,
    create_chrome_session,
)

_CRAWL4AI_AVAILABLE = False
try:
    from crawl4ai import AsyncWebCrawler

    _CRAWL4AI_AVAILABLE = True
except ImportError:
    pass


_EXTRACT_SYSTEM = (
    "You are a web review extractor. Given the Markdown content of a webpage, "
    "extract all user comments, reviews, or forum posts about the topic. "
    "Return a JSON array of objects with keys: author, date, rating (null if N/A), text. "
    "Only include real user-generated content — not ads, navigation, or boilerplate. "
    "Return an empty array [] if no genuine user reviews are found."
)

_TWEET_RE = re.compile(r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)", re.IGNORECASE)

_REVIEW_CONTAINER_SELECTORS = (
    '[itemprop="review"]',
    '[itemprop="comment"]',
    "article.comment",
    ".commentlist > li",
    ".comment-list > li",
    ".comment-body",
    ".review-card",
    ".review-item",
    ".customer-review",
    ".user-review",
    ".ugc-review",
    ".jdgm-rev",
    ".yotpo-review",
    ".user-thread",
    "article.message",
    ".tp-widget-review",
    "[data-review-id]",
)

_NEXT_PAGE_SELECTORS = (
    'a[rel="next"]',
    'link[rel="next"]',
    ".pagination a.next",
    "a.next.page-numbers",
    "li.next a",
    'a[aria-label="Next page"]',
)


# ── Twitter / X Public JSON Collector ────────────────────────────────────────


def _collect_twitter_sync(source: DiscoveredSource, max_items: int | None) -> list[RawComment]:
    m = _TWEET_RE.search(source.url)
    if not m:
        return []
    user, tweet_id = m.groups()
    sess = create_chrome_session()
    if sess is None:
        return []

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    try:
        fx = sess.get(f"https://api.fxtwitter.com/{user}/status/{tweet_id}", headers={"Accept": "application/json"})
        if fx.status_code == 200:
            data = fx.json()
            tweet = data.get("tweet") or {}
            author_name = (tweet.get("author") or {}).get("screen_name") or user
            text = str(tweet.get("text") or "").strip()
            if text:
                append_unique_comment(
                    comments=comments,
                    seen_keys=seen_keys,
                    comment_id=f"x_{tweet_id}",
                    author=f"@{author_name}",
                    text=text,
                    published_date=tweet.get("created_timestamp") or tweet.get("created_at") or "",
                    rating=None,
                    platform="Twitter",
                    source=source,
                )
            quote = tweet.get("quote") or {}
            if isinstance(quote, dict) and quote.get("text") and (max_items is None or len(comments) < max_items):
                q_auth = (quote.get("author") or {}).get("screen_name") or "quoted_user"
                append_unique_comment(
                    comments=comments,
                    seen_keys=seen_keys,
                    comment_id=f"x_q_{quote.get('id') or tweet_id}",
                    author=f"@{q_auth}",
                    text=str(quote.get("text") or ""),
                    published_date=quote.get("created_timestamp") or "",
                    rating=None,
                    platform="Twitter",
                    source=source,
                )

        if not comments:
            syn = sess.get(
                f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=x",
                headers={"Accept": "application/json"},
            )
            if syn.status_code == 200:
                tdata = syn.json()
                ttext = str(tdata.get("text") or "").strip()
                tuser = (tdata.get("user") or {}).get("screen_name") or user
                if ttext:
                    append_unique_comment(
                        comments=comments,
                        seen_keys=seen_keys,
                        comment_id=f"x_syn_{tweet_id}",
                        author=f"@{tuser}",
                        text=ttext,
                        published_date=tdata.get("created_at") or "",
                        rating=None,
                        platform="Twitter",
                        source=source,
                    )

        if not comments:
            vx = sess.get(
                f"https://api.vxtwitter.com/{user}/status/{tweet_id}",
                headers={"Accept": "application/json"},
            )
            if vx.status_code == 200 and (vx.text or "").strip().startswith("{"):
                vdata = vx.json()
                vtext = str(vdata.get("text") or "").strip()
                vuser = str(vdata.get("user_screen_name") or user)
                if vtext:
                    append_unique_comment(
                        comments=comments,
                        seen_keys=seen_keys,
                        comment_id=f"x_vx_{tweet_id}",
                        author=f"@{vuser}",
                        text=vtext,
                        published_date=vdata.get("date_epoch") or vdata.get("date") or "",
                        rating=None,
                        platform="Twitter",
                        source=source,
                    )
                qrt = vdata.get("qrt") or {}
                if isinstance(qrt, dict) and qrt.get("text") and (max_items is None or len(comments) < max_items):
                    append_unique_comment(
                        comments=comments,
                        seen_keys=seen_keys,
                        comment_id=f"x_vx_q_{qrt.get('tweetID') or tweet_id}",
                        author=f"@{qrt.get('user_screen_name') or 'quoted_user'}",
                        text=str(qrt.get("text") or ""),
                        published_date=qrt.get("date_epoch") or qrt.get("date") or "",
                        rating=None,
                        platform="Twitter",
                        source=source,
                    )
    except Exception as e:
        logger.debug(f"FxTwitter/syndication lookup failed for {source.url}: {e}")
    finally:
        try:
            sess.close()
        except Exception:
            pass
    return comments


# ── Discourse Forum JSON Collector ───────────────────────────────────────────


def _collect_discourse_json_sync(source: DiscoveredSource, max_items: int | None) -> list[RawComment]:
    if "/t/" not in urlparse(source.url).path:
        return []
    clean = source.url.split("?")[0].rstrip("/")
    json_url = clean if clean.endswith(".json") else f"{clean}.json"
    sess = create_chrome_session(timeout=12)
    if sess is None:
        return []

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    try:
        r = sess.get(json_url, headers={"Accept": "application/json"})
        if r.status_code != 200 or "json" not in (r.headers.get("content-type") or "").lower():
            return []
        data = r.json()
        posts = (data.get("post_stream") or {}).get("posts") or []
        for p in posts:
            if max_items is not None and len(comments) >= max_items:
                break
            cooked = str(p.get("cooked") or "")
            if not cooked:
                continue
            text = BeautifulSoup(cooked, "html.parser").get_text(" ", strip=True)
            append_unique_comment(
                comments=comments,
                seen_keys=seen_keys,
                comment_id=f"disc_{p.get('id') or len(comments)}",
                author=str(p.get("username") or p.get("name") or "Forum Member"),
                text=text,
                published_date=p.get("created_at") or "",
                rating=None,
                platform=source.platform,
                source=source,
            )
    except Exception:
        return []
    finally:
        try:
            sess.close()
        except Exception:
            pass
    return comments


# ── Structured HTML Extractors (JSON-LD + DOM + Trustpilot __NEXT_DATA__) ───


def _extract_jsonld_items(html: str, source: DiscoveredSource, seen_keys: set[str], comments: list[RawComment], max_items: int | None) -> None:
    soup = BeautifulSoup(html or "", "html.parser")
    for sc in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(sc.string or "{}")
        except Exception:
            continue

        def _walk_ld(obj: Any) -> None:
            if max_items is not None and len(comments) >= max_items:
                return
            if isinstance(obj, dict):
                obj_type = str(obj.get("@type") or "").lower()
                if obj_type in ("review", "comment", "usercomments", "socialmediaposting"):
                    text = str(obj.get("reviewBody") or obj.get("text") or obj.get("description") or "").strip()
                    name = str(obj.get("headline") or obj.get("name") or "").strip()
                    full = text if not name or name.lower() in text.lower() else f"{name} — {text}"
                    auth = obj.get("author")
                    author = auth.get("name") if isinstance(auth, dict) else str(auth or "Reviewer")
                    rr = obj.get("reviewRating") or {}
                    rating = rr.get("ratingValue") if isinstance(rr, dict) else None
                    append_unique_comment(
                        comments=comments,
                        seen_keys=seen_keys,
                        comment_id=f"ld_{len(comments)}_{abs(hash(full)) % 100000}",
                        author=author,
                        text=full,
                        published_date=obj.get("datePublished") or obj.get("dateCreated") or "",
                        rating=rating,
                        platform=source.platform,
                        source=source,
                    )
                for key in ("review", "comment", "mainEntity", "itemListElement", "@graph"):
                    if key in obj:
                        _walk_ld(obj[key])
            elif isinstance(obj, list):
                for item in obj:
                    _walk_ld(item)

        _walk_ld(payload)

    # Trustpilot __NEXT_DATA__ reviews
    next_script = soup.select_one("script#__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            nd = json.loads(next_script.string)
            revs = ((nd.get("props") or {}).get("pageProps") or {}).get("reviews") or []
            for r in revs:
                if max_items is not None and len(comments) >= max_items:
                    break
                if not isinstance(r, dict):
                    continue
                consumer = r.get("consumer") or {}
                author = consumer.get("displayName") if isinstance(consumer, dict) else "Trustpilot Reviewer"
                dates = r.get("dates") or {}
                pub = dates.get("publishedDate") if isinstance(dates, dict) else ""
                title = str(r.get("title") or "").strip()
                body = str(r.get("text") or "").strip()
                full = f"{title} — {body}".strip(" —") if title and title.lower() not in body.lower() else body
                append_unique_comment(
                    comments=comments,
                    seen_keys=seen_keys,
                    comment_id=str(r.get("id") or f"tp_{len(comments)}"),
                    author=str(author or "Trustpilot Reviewer"),
                    text=full,
                    published_date=str(pub or ""),
                    rating=r.get("rating"),
                    platform=source.platform,
                    source=source,
                )
        except Exception:
            pass


def _extract_dom_reviews_or_comments(
    html: str,
    source: DiscoveredSource,
    seen_keys: set[str],
    comments: list[RawComment],
    max_items: int | None,
) -> list[str]:
    """Extract structured DOM review/comment elements and return discovered next-page URLs."""
    soup = BeautifulSoup(html or "", "html.parser")
    next_urls: list[str] = []
    for sel in _NEXT_PAGE_SELECTORS:
        for el in soup.select(sel):
            href = el.get("href")
            if href:
                next_urls.append(urljoin(source.url, str(href)))

    # Parse structured containers cleanly
    for sel in _REVIEW_CONTAINER_SELECTORS:
        nodes = soup.select(sel)
        if not nodes:
            continue
        for idx, node in enumerate(nodes):
            if max_items is not None and len(comments) >= max_items:
                break
            # Skip nested container already processed
            author_el = node.select_one('[itemprop="author"], .fn, .comment-author, .author, .username, .u-name, .jdgm-rev__author')
            author = author_el.get_text(" ", strip=True) if author_el else "Reviewer"

            date_el = node.select_one('time, [itemprop="datePublished"], .comment-meta a, .date, .jdgm-rev__timestamp')
            pub_date = ""
            if date_el:
                pub_date = str(date_el.get("datetime") or date_el.get_text(" ", strip=True))

            rating = None
            r_el = node.select_one('[itemprop="ratingValue"], [data-score], [data-rating], .star-rating, .rating')
            if r_el:
                rating = clamp_rating(
                    r_el.get("content") or r_el.get("data-score") or r_el.get("data-rating") or r_el.get("aria-label") or r_el.get_text(" ", strip=True)
                )

            body_el = node.select_one('[itemprop="reviewBody"], [itemprop="text"], .comment-content, .review-body, .review-text, .jdgm-rev__body, .bbWrapper, p')
            body = body_el.get_text(" ", strip=True) if body_el else node.get_text(" ", strip=True)
            title_el = node.select_one('[itemprop="name"], .review-title, .jdgm-rev__title, h3, h4')
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if title and title.lower() not in body.lower()[: len(title) + 12]:
                body = f"{title} — {body}"

            if len(body) >= 12:
                append_unique_comment(
                    comments=comments,
                    seen_keys=seen_keys,
                    comment_id=f"dom_{len(comments)}_{idx}_{abs(hash(body)) % 100000}",
                    author=author,
                    text=body,
                    published_date=pub_date,
                    rating=rating,
                    platform=source.platform,
                    source=source,
                    min_len=12,
                )

    return next_urls


# ── Page Fetchers (curl_cffi IPv4 → crawl4ai → aiohttp) ──────────────────────


def _html_to_clean_text(html: str, max_chars: int = 45000) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    lines = [el.get_text(" ", strip=True) for el in soup.find_all(["h1", "h2", "h3", "p", "li", "article", "blockquote"])]
    joined = "\n\n".join(line for line in lines if line)
    if len(joined) < 100:
        joined = " ".join(soup.stripped_strings)
    return joined[:max_chars]


async def _fetch_html_and_markdown(url: str) -> tuple[str, str]:
    """Fetch raw HTML and readable text/markdown via Chrome TLS, aiohttp, or crawl4ai."""
    html = ""
    markdown = ""

    loop = asyncio.get_running_loop()

    def _fetch_chrome():
        sess = create_chrome_session(timeout=min(settings.request_timeout, 18))
        if sess is None:
            return 0, ""
        try:
            r = sess.get(url, headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"})
            if r.status_code == 200:
                return 200, str(r.text or "")
            return r.status_code, ""
        except Exception:
            return 0, ""
        finally:
            try:
                sess.close()
            except Exception:
                pass

    status, html = await loop.run_in_executor(None, _fetch_chrome)
    if html:
        markdown = _html_to_clean_text(html)

    # If Chrome HTTP got blocked (e.g. Cloudflare/JS challenge) or empty text, try crawl4ai Playwright
    if (not html or len(markdown.strip()) < 120) and _CRAWL4AI_AVAILABLE:
        try:
            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await asyncio.wait_for(crawler.arun(url=url), timeout=18)
                c_html = str(getattr(result, "html", "") or "")
                c_md = str(getattr(result, "markdown", None) or getattr(result, "markdown_v2", None) or "")
                if c_html and len(c_html) > len(html):
                    html = c_html
                if len(c_md.strip()) > len(markdown.strip()):
                    markdown = c_md[:45000]
        except Exception as e:
            logger.debug(f"crawl4ai fallback note on {url}: {e}")

    # Last fallback: aiohttp
    if not html and not markdown:
        try:
            import aiohttp

            connector = aiohttp.TCPConnector()
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            req_timeout = min(settings.request_timeout, 12)
            async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=req_timeout)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        markdown = _html_to_clean_text(html)
        except Exception as e:
            logger.debug(f"Web aiohttp fallback failed for {url}: {e}")

    return html, markdown


async def _crawl_markdown(url: str) -> str:
    """Compatibility helper returning clean markdown/text for a URL."""
    _html, md = await _fetch_html_and_markdown(url)
    return md


_DISCUSSION_URL_OR_TITLE_RE = re.compile(
    r"(?:review|reviews|rating|ratings|forum|comment|comments|discuss|discussion|community|reddit|quora|trustpilot|ambitionbox|glassdoor|compar|feedback|opinion|pros|cons|g2\.com|capterra|blog|/t/|/p/)",
    re.IGNORECASE,
)


def _is_likely_review_or_discussion_page(source: DiscoveredSource, markdown: str) -> bool:
    """Guard heuristic fallback so non-review corporate homepages do not turn PR slogans into fake comments."""
    if source.is_target or source.platform in ("Blog", "Forum", "Twitter", "Reddit"):
        return True
    if _DISCUSSION_URL_OR_TITLE_RE.search(f"{source.url} {source.title or ''}"):
        return True
    if re.search(r"\b(?:customer reviews|verified purchase|user reviews|top reviews|community comments)\b", markdown, re.IGNORECASE):
        return True
    author_prefixed = sum(
        1
        for blk in re.split(r"\n{2,}", markdown[:12000])
        if re.match(r"^(@?[A-Za-z0-9_\-\.]{3,20})\s*(?:says|wrote|said|:|posted)\s*[-—:]\s*\S+", " ".join(blk.split()))
    )
    return author_prefixed >= 2


def _heuristic_extract_web_comments(markdown: str, url: str, max_items: int | None = None) -> list[dict]:
    """Smart heuristic extractor to gather discussion posts & review paragraphs without an LLM."""
    if not markdown:
        return []

    ignore_patterns = re.compile(
        r"(privacy policy|terms of (use|service)|cookie (policy|preferences|settings)|all rights reserved"
        r"|sign in|create an account|forgot password|log in|passwordless|copyright \d{4}"
        r"|subscribe to our newsletter|leave a reply|cancel reply|share this post"
        r"|powered by|designed by|search\.\.\.|click here to|advertisement|download app"
        r"|skip to (?:main )?content|apply now|explore careers|our podcasts keep|future is yours to build"
        r"|building together|modernize technology|shape the future with)",
        re.IGNORECASE,
    )

    blocks = re.split(r"\n{2,}|(?<=[.!?])\s{2,}", markdown)
    extracted: list[dict] = []
    seen_texts: set[str] = set()

    for block in blocks:
        clean = " ".join(block.split()).strip()
        clean = re.sub(r"^([>#*\-+\s]+)", "", clean).strip()

        if len(clean) < 25 or len(clean) > 1000:
            continue
        if ignore_patterns.search(clean):
            continue

        author = "Community User"
        m_author = re.match(r"^(@?[A-Za-z0-9_\-\.]{3,20})\s*(?:says|wrote|said|:|posted)?\s*[-—:]\s*(.+)", clean)
        text = clean
        if m_author:
            cand_author, cand_text = m_author.groups()
            if len(cand_text) >= 20:
                author = cand_author
                text = cand_text

        key = text.lower()
        if key in seen_texts:
            continue
        seen_texts.add(key)

        extracted.append({"author": author, "text": text, "date": "", "rating": None})
        if max_items is not None and len(extracted) >= max_items:
            break

    return extracted


async def _llm_extract_comments(markdown: str, url: str, platform: str, max_items: int | None = None) -> list[dict]:
    """Call LLM when configured, otherwise fall back to full heuristic parser."""
    if not markdown.strip():
        return []

    try:
        from stages._llm_client import get_llm_client

        client = get_llm_client()
        user_msg = f"Page URL: {url}\nPlatform: {platform}\n\nMarkdown Content:\n{markdown[:14000]}"
        raw = await client.generate(system=_EXTRACT_SYSTEM, user=user_msg, model_tier="flash")
        raw_clean = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
        json_match = re.search(r"\[.*\]", raw_clean, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, list) and parsed:
                    return parsed[:max_items] if max_items is not None else parsed
            except json.JSONDecodeError:
                pass
        try:
            parsed = json.loads(raw_clean)
            if isinstance(parsed, list) and parsed:
                return parsed[:max_items] if max_items is not None else parsed
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"LLM extraction not available for {url} ({e}) — using built-in smart web text parser")

    return _heuristic_extract_web_comments(markdown, url, max_items=max_items)


async def collect_web(
    source: DiscoveredSource,
) -> tuple[list[RawComment], DiscoveredSource]:
    """Extract reviews/comments from Twitter/X, forums, blogs, and general web pages."""
    limit = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    cache = get_cache()
    cache_key = f"v3::{source.url}::{settings.max_comments_per_url}"
    cached = cache.get("s3_web", cache_key)
    if cached is not None and isinstance(cached, list):
        comments = [RawComment.model_validate({**c, "is_target": source.is_target}) for c in cached]
        return comments, source.model_copy(
            update={
                "scrape_status": "success" if comments else "partial",
                "collector_used": "web_scraper (cached)",
                "comments_collected": len(comments),
            }
        )

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    collector_name = "web_scraper"
    loop = asyncio.get_running_loop()

    # Layer 1: Twitter / X JSON endpoints (bypasses X login/JS requirement cleanly)
    if source.platform == "Twitter" or _TWEET_RE.search(source.url):
        tw_comments = await loop.run_in_executor(None, _collect_twitter_sync, source, limit)
        if tw_comments:
            comments.extend(tw_comments[:limit] if limit is not None else tw_comments)
            collector_name = "fxtwitter_api"

    # Layer 2: Discourse forum `.json` topic API
    if not comments and "/t/" in urlparse(source.url).path:
        disc_comments = await loop.run_in_executor(None, _collect_discourse_json_sync, source, limit)
        if disc_comments:
            comments.extend(disc_comments[:limit] if limit is not None else disc_comments)
            collector_name = "discourse_json"

    # Layer 3: Multi-page structured HTML (JSON-LD + DOM selectors) + LLM/heuristic fallback
    if not comments:
        visited_urls: set[str] = {source.url}
        to_visit: list[str] = [source.url]
        all_markdown_chunks: list[str] = []
        max_pages = 200 if limit is None else max(10, min(500, (limit // 10) + 2))

        for _ in range(max_pages):
            if not to_visit or (limit is not None and len(comments) >= limit):
                break
            curr_url = to_visit.pop(0)
            html, md = await _fetch_html_and_markdown(curr_url)
            if md:
                all_markdown_chunks.append(md)
            if not html:
                continue

            before = len(comments)
            _extract_jsonld_items(html, source, seen_keys, comments, limit)
            next_links = _extract_dom_reviews_or_comments(html, source, seen_keys, comments, limit)
            if len(comments) > before:
                collector_name = "web_structured"

            # Follow explicit rel="next" pagination links only when structured reviews were found
            if len(comments) > before:
                for nxt in next_links:
                    if nxt not in visited_urls and urlparse(nxt).netloc == urlparse(source.url).netloc:
                        visited_urls.add(nxt)
                        to_visit.append(nxt)
                        break

        if not comments and all_markdown_chunks:
            combined_md = "\n\n".join(all_markdown_chunks)
            if _is_likely_review_or_discussion_page(source, combined_md):
                extracted = await _llm_extract_comments(combined_md, source.url, str(source.platform), max_items=limit)
                collector_name = "crawl4ai+llm" if (settings.has_gemini or settings.has_openai) else "web_scraper"
                for idx, item in enumerate(extracted[:limit]):
                    append_unique_comment(
                        comments=comments,
                        seen_keys=seen_keys,
                        comment_id=f"web_{idx}_{abs(hash(str(item.get('text', '')))) % 100000}",
                        author=str(item.get("author", "Community User")),
                        text=str(item.get("text", "")),
                        published_date=str(item.get("date", "")),
                        rating=item.get("rating"),
                        platform=source.platform,
                        source=source,
                    )

    if limit is not None and len(comments) > limit:
        comments = comments[:limit]

    if not comments:
        return [], source.model_copy(
            update={
                "scrape_status": "failed",
                "collector_used": collector_name,
                "comments_collected": 0,
                "error_message": "No public reviews/comments extracted (site may block bots or load comments dynamically)",
            }
        )

    updated = source.model_copy(
        update={
            "scrape_status": "success",
            "collector_used": collector_name,
            "comments_collected": len(comments),
            "error_message": "",
        }
    )
    cache.set("s3_web", cache_key, [c.model_dump() for c in comments])
    logger.info(f"Web ({collector_name}): {len(comments)} comments from {source.url}")
    return comments, updated
