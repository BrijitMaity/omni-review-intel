"""
Stage 3 Collector — Flipkart Product Reviews
============================================
Extracts and concurrently paginates verified customer reviews directly from
Flipkart product & `/product-reviews/` pages via Chrome TLS impersonation
(`window.__INITIAL_STATE__` `ProductReviewValue` JSON + DOM fallback) with zero API keys.
"""

from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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


_FK_ITEM_RE = re.compile(r"/(?:p|product-reviews)/(itm[A-Za-z0-9]+)", re.IGNORECASE)


def _extract_flipkart_item_id(url: str) -> str | None:
    """Extract Flipkart's `itm...` item ID or `pid=` parameter if present."""
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host and "flipkart." not in host and "fkrt." not in host:
        return None
    m = _FK_ITEM_RE.search(parsed.path)
    if m:
        return m.group(1)
    qs = parse_qs(parsed.query)
    if qs.get("pid"):
        return qs["pid"][0]
    return None


def _to_reviews_page_url(url: str, page: int = 1) -> str:
    """Convert any Flipkart `/p/...` product URL to its paginated `/product-reviews/...` URL."""
    parsed = urlparse(url)
    path = parsed.path
    if "/p/" in path and "/product-reviews/" not in path:
        path = path.replace("/p/", "/product-reviews/", 1)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    qs.pop("page", None)
    clean_pairs: list[tuple[str, str]] = [(k, vals[0]) for k, vals in qs.items() if vals]
    if page > 1:
        clean_pairs.append(("page", str(page)))
    return urlunparse(parsed._replace(path=path, query=urlencode(clean_pairs), fragment=""))


def _extract_initial_state_json(html: str) -> dict[str, Any]:
    """Locate and parse Flipkart's `window.__INITIAL_STATE__` JSON payload."""
    marker = "window.__INITIAL_STATE__"
    idx = (html or "").find(marker)
    if idx == -1:
        return {}
    sub = html[idx + len(marker) :]
    start = sub.find("{")
    if start == -1:
        return {}
    sub = sub[start:]
    end = sub.find(";</script>")
    if end != -1:
        try:
            return json.loads(sub[:end])
        except Exception:
            pass
    # Fallback brace scanner if </script> marker differs
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(sub)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _parse_flipkart_reviews_html(html: str, source: DiscoveredSource) -> dict[str, Any]:
    """Parse Flipkart customer reviews and total review count from HTML/__INITIAL_STATE__."""
    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    total_reported = 0

    data = _extract_initial_state_json(html)

    def _walk(obj: Any) -> None:
        nonlocal total_reported
        if isinstance(obj, dict):
            if obj.get("totalReviewCount") is not None:
                try:
                    total_reported = max(total_reported, int(obj["totalReviewCount"]))
                except (TypeError, ValueError):
                    pass
            if isinstance(obj.get("reviewCount"), int):
                total_reported = max(total_reported, obj["reviewCount"])

            is_review = obj.get("type") == "ProductReviewValue" or (
                "text" in obj
                and ("rating" in obj or "author" in obj)
                and any(k in obj for k in ("author", "certifiedBuyer", "created"))
            )
            if is_review:
                title = str(obj.get("title") or "").strip()
                body = str(obj.get("text") or "").strip()
                full_text = body if not title or title.lower() in body.lower()[: len(title) + 10] else f"{title} — {body}"
                append_unique_comment(
                    comments=comments,
                    seen_keys=seen_keys,
                    comment_id=str(obj.get("id") or f"fk_{len(comments)}"),
                    author=str(obj.get("author") or "Flipkart Customer"),
                    text=full_text,
                    published_date=str(obj.get("created") or ""),
                    rating=obj.get("rating"),
                    platform="Flipkart",
                    source=source,
                )

            for v in obj.values():
                if isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                if isinstance(x, (dict, list)):
                    _walk(x)

    if data:
        _walk(data)

    # DOM fallback if __INITIAL_STATE__ absent or yielded no review cards
    if not comments and html:
        soup = BeautifulSoup(html, "html.parser")
        for idx, card in enumerate(soup.select('div[class*="cPHDOP"] div.col, div._27M-vq, div.EKFha-')):
            body_el = card.select_one("div.ZmyHeo, div.t-ZTKy, div._11pzQk")
            if not body_el:
                continue
            body = re.sub(r"\s*READ MORE\s*$", "", body_el.get_text(" ", strip=True), flags=re.IGNORECASE)
            title_el = card.select_one("p.z9E0IG, p._2-N8zT")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            full_text = f"{title} — {body}".strip(" —") if title and title.lower() not in body.lower() else body
            rating_el = card.select_one("div.XQDdHH, div._3LWZlK")
            author_el = card.select_one("p._2NsDsF.AwS1CA, p._2sc7ZR._2V5EHH")
            append_unique_comment(
                comments=comments,
                seen_keys=seen_keys,
                comment_id=f"fk_dom_{idx}_{abs(hash(full_text)) % 100000}",
                author=author_el.get_text(" ", strip=True) if author_el else "Flipkart Customer",
                text=full_text,
                published_date="",
                rating=clamp_rating(rating_el.get_text(" ", strip=True) if rating_el else None),
                platform="Flipkart",
                source=source,
            )

    return {
        "comments": comments,
        "total_reported": total_reported,
    }


def _extract_flipkart_view_urls(html: str, reviews_base_url: str, item_id: str | None) -> list[str]:
    """Build distinct Flipkart sortOrder and aspect-filter URLs to bypass the ~10-page default cap."""
    parsed_base = urlparse(reviews_base_url)
    base_origin = f"{parsed_base.scheme or 'https'}://{parsed_base.netloc or 'www.flipkart.com'}"
    candidates: list[str] = []

    if html:
        pattern = (
            rf"/product-reviews/{re.escape(item_id)}[^\"'\\\s<>]*"
            if item_id
            else r"/product-reviews/itm[A-Za-z0-9]+[^\"'\\\s<>]*"
        )
        for rel in re.findall(pattern, html, flags=re.IGNORECASE):
            clean_rel = rel.replace("\\u0026", "&").replace("&amp;", "&")
            full = clean_rel if clean_rel.startswith("http") else f"{base_origin}{clean_rel}"
            candidates.append(_to_reviews_page_url(full, page=1))

    # Add core sort modes on base URL in case __INITIAL_STATE__ omitted sort pills
    parsed_q = parse_qs(parsed_base.query, keep_blank_values=False)
    base_clean = urlunparse(parsed_base._replace(query="", fragment=""))
    for sort_order in ("MOST_RECENT", "POSITIVE_FIRST", "NEGATIVE_FIRST", "MOST_HELPFUL"):
        q_items = {k: v[0] for k, v in parsed_q.items() if v and k not in ("page", "sortOrder", "aid", "certifiedBuyer")}
        q_items["aid"] = "overall"
        q_items["certifiedBuyer"] = "false"
        q_items["sortOrder"] = sort_order
        candidates.append(f"{base_clean}?{urlencode(q_items)}")

    deduped: list[str] = []
    seen_sigs: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    base_sig = (parsed_base.path, tuple(sorted((k, v[0]) for k, v in parsed_q.items() if v and k != "page")))
    seen_sigs.add(base_sig)
    for u in candidates:
        pu = urlparse(u)
        qs = parse_qs(pu.query, keep_blank_values=False)
        if not qs:
            continue
        sig = (pu.path, tuple(sorted((k, v[0]) for k, v in qs.items() if v and k != "page")))
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped.append(u)
    return deduped


def _collect_flipkart_sync(
    source: DiscoveredSource,
    max_items: int | None,
) -> dict[str, Any]:
    """Resolve shortlinks/product links and paginate Flipkart reviews + sort/aspect facets concurrently."""
    sess = create_chrome_session()
    if sess is None:
        return {"comments": [], "total_reported": 0}

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    total_reported = 0
    headers = {"Accept-Language": "en-US,en;q=0.9"}
    req_timeout = float(max(settings.request_timeout, 15))

    def _merge(batch_res: dict[str, Any]) -> int:
        nonlocal total_reported
        total_reported = max(total_reported, batch_res["total_reported"])
        added = 0
        for c in batch_res["comments"]:
            if max_items is not None and len(comments) >= max_items:
                break
            if append_unique_comment(
                comments=comments,
                seen_keys=seen_keys,
                comment_id=c.comment_id,
                author=c.author,
                text=c.original_text,
                published_date=c.published_date,
                rating=c.rating,
                platform="Flipkart",
                source=source,
            ):
                added += 1
        return added

    def _paginate_view(view_url: str, page1_resp_obj: Any = None, max_view_pages: int = 10000) -> str:
        first_html = ""
        if page1_resp_obj is not None:
            r1 = page1_resp_obj
        else:
            r1 = sess.get(_to_reviews_page_url(view_url, page=1), headers=headers)
        if getattr(r1, "status_code", 0) != 200 or not getattr(r1, "text", ""):
            return ""
        first_html = r1.text
        first_parsed = _parse_flipkart_reviews_html(first_html, source)
        _merge(first_parsed)
        if not first_parsed["comments"]:
            return first_html

        per_page = max(len(first_parsed["comments"]), 10)
        if max_items is not None:
            remaining = max(0, max_items - len(comments))
            needed_pages = min((remaining + per_page - 1) // per_page + 2, max_view_pages)
        elif total_reported:
            needed_pages = min((total_reported + per_page - 1) // per_page + 2, max_view_pages)
        else:
            needed_pages = max_view_pages

        if needed_pages > 1 and (max_items is None or len(comments) < max_items):
            def _fetch_page(page_num: int) -> dict[str, Any]:
                s = create_chrome_session(timeout=req_timeout)
                if s is None:
                    return {"comments": [], "total_reported": 0}
                try:
                    u = _to_reviews_page_url(view_url, page=page_num)
                    r = s.get(u, headers=headers)
                    if r.status_code == 200 and r.text:
                        return _parse_flipkart_reviews_html(r.text, source)
                except Exception:
                    pass
                finally:
                    try:
                        s.close()
                    except Exception:
                        pass
                return {"comments": [], "total_reported": 0}

            batch_size = 8
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                for start_page in range(2, needed_pages + 1, batch_size):
                    if max_items is not None and len(comments) >= max_items:
                        break
                    pages = list(range(start_page, min(start_page + batch_size, needed_pages + 1)))
                    batch_results = list(pool.map(_fetch_page, pages))
                    batch_parsed_total = sum(len(res["comments"]) for res in batch_results)
                    batch_added = 0
                    for res in batch_results:
                        batch_added += _merge(res)
                    if batch_added == 0 or batch_parsed_total == 0:
                        break
        return first_html

    try:
        # Resolve dl.flipkart.com / fkrt.it shortlinks or product page redirects first
        init_resp = sess.get(source.url, headers=headers)
        final_url = str(getattr(init_resp, "url", source.url))
        reviews_base_url = _to_reviews_page_url(final_url, page=1)

        if reviews_base_url != final_url:
            page1_resp = sess.get(reviews_base_url, headers=headers)
        else:
            page1_resp = init_resp

        first_html = _paginate_view(reviews_base_url, page1_resp_obj=page1_resp, max_view_pages=10000)

        # Flipkart caps a single review view around page 10-11 (~101 reviews).
        # If total_reported exceeds collected reviews and limit permits, sweep sortOrder & aspect filters (`aid=`).
        needs_more = (
            bool(first_html)
            and (max_items is None or len(comments) < max_items)
            and total_reported > len(comments)
        )
        if needs_more:
            item_id = _extract_flipkart_item_id(reviews_base_url)
            alt_views = _extract_flipkart_view_urls(first_html, reviews_base_url, item_id)
            for alt_url in alt_views[:20]:
                if (max_items is not None and len(comments) >= max_items) or (
                    total_reported and len(comments) >= total_reported
                ):
                    break
                _paginate_view(alt_url, page1_resp_obj=None, max_view_pages=12)

    except Exception as e:
        logger.warning(f"Flipkart scrape failed for {source.url}: {e}")
    finally:
        try:
            sess.close()
        except Exception:
            pass

    return {
        "comments": comments,
        "total_reported": total_reported,
    }


async def collect_flipkart(source: DiscoveredSource) -> tuple[list[RawComment], DiscoveredSource]:
    """Collect verified Flipkart customer reviews without any API key."""
    item_id = _extract_flipkart_item_id(source.url)
    max_items: int | None = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    cache_id = f"flipkart_v3::{item_id or source.url}::{settings.max_comments_per_url}"

    cache = get_cache()
    cached = cache.get("s3_flipkart", cache_id)
    if cached is not None and isinstance(cached, dict):
        cached_status = cached.get("scrape_status", "success")
        if not (max_items is None and cached_status == "partial"):
            restored = [
                RawComment(**{**item, "is_target": source.is_target})
                for item in cached.get("comments", [])
            ]
            updated = source.model_copy(
                update={
                    "comments_collected": len(restored),
                    "scrape_status": cached_status,
                    "collector_used": cached.get("collector_used", "flipkart (cached)"),
                    "error_message": cached.get("error_message", ""),
                }
            )
            return restored, updated

    res = await asyncio.to_thread(_collect_flipkart_sync, source, max_items)
    comments: list[RawComment] = res["comments"]
    total_reported: int = res["total_reported"]

    if max_items is not None and len(comments) > max_items:
        comments = comments[:max_items]

    if not comments:
        return [], source.model_copy(
            update={
                "scrape_status": "failed",
                "collector_used": "flipkart_reviews",
                "comments_collected": 0,
                "error_message": "No customer reviews found on Flipkart page",
            }
        )

    hit_user_cap = max_items is not None and len(comments) >= max_items
    is_partial = bool(total_reported and total_reported > len(comments) and not hit_user_cap)
    status = "partial" if is_partial else "success"
    err = f"Collected {len(comments)} of ~{total_reported:,} Flipkart reviews" if is_partial else ""

    updated_source = source.model_copy(
        update={
            "scrape_status": status,
            "collector_used": "flipkart_reviews",
            "comments_collected": len(comments),
            "error_message": err,
        }
    )
    cache.set(
        "s3_flipkart",
        cache_id,
        {
            "comments": [c.model_dump() for c in comments],
            "scrape_status": updated_source.scrape_status,
            "collector_used": updated_source.collector_used,
            "error_message": updated_source.error_message,
        },
    )
    return comments, updated_source
