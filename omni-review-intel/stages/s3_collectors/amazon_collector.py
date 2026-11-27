"""
Stage 3 Collector — Amazon Product & Customer Reviews
=====================================================
Multi-layer Amazon extraction:
  1. Apify API (`APIFY_API_KEY`), when configured (full pagination).
  2. Optional authenticated `/product-reviews/{asin}/` pagination via `AMAZON_COOKIE`.
  3. Zero-key product page (`/dp/{asin}`) + public review DOM & JSON-LD extraction
     via Chrome TLS impersonation (`curl_cffi`, IPv4), supporting both modern
     `data-hook="reviewText"` / `reviewTitle` and legacy `review-body` / `review-title` markup.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

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
from stages.s3_collectors.apify_collector import collect_apify


_ASIN_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|gp/aw/reviews|product-reviews)/([A-Z0-9]{10})(?:[/?#]|$)",
    re.IGNORECASE,
)


def _extract_asin(url: str) -> str | None:
    """Extract the 10-character Amazon ASIN from a product or reviews URL."""
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host and not any(tok in host for tok in ("amazon.", "amzn.", "a.co")):
        return None
    m = _ASIN_RE.search(url)
    return m.group(1).upper() if m else None


def _clean_review_date(raw_date: str) -> str:
    txt = re.sub(r"^Reviewed in .+?\s+on\s+", "", (raw_date or "").strip(), flags=re.IGNORECASE)
    return txt.strip()


def _parse_total_amazon_reviews(soup: BeautifulSoup) -> int:
    for sel in (
        '[data-hook="cr-filter-info-review-rating-count"]',
        '[data-hook="total-review-count"]',
        "#acrCustomerReviewText",
    ):
        el = soup.select_one(sel)
        if not el:
            continue
        txt = el.get_text(" ", strip=True)
        m_rev = re.search(r"([\d,]+)\s+(?:with\s+reviews|customer\s+reviews|global\s+ratings|ratings)", txt, re.IGNORECASE)
        if m_rev:
            try:
                return int(m_rev.group(1).replace(",", ""))
            except ValueError:
                pass
        nums = [int(n.replace(",", "")) for n in re.findall(r"\b\d[\d,]*\b", txt)]
        if nums:
            return max(nums)
    return 0


def _parse_amazon_reviews_html(
    html: str,
    asin: str,
    source: DiscoveredSource,
) -> dict[str, Any]:
    """Extract Amazon reviews (DOM + JSON-LD) and block/total indicators."""
    soup = BeautifulSoup(html or "", "html.parser")
    comments: list[RawComment] = []
    seen_keys: set[str] = set()

    total_reported = _parse_total_amazon_reviews(soup)
    signin_walled = bool(soup.select_one('form[name="signIn"], #ap_email, #ap_password'))
    captcha_walled = (
        "validateCaptcha" in (html or "")
        or "Enter the characters you see below" in (html or "")
    )

    for rv in soup.select('[data-hook="review"]'):
        # Strip Amazon UI expand/collapse screen-reader boilerplate before extracting body text
        for noise in rv.select(".a-teaser-describedby-collapsed, .a-teaser-describedby-expanded"):
            noise.decompose()

        rev_id = str(rv.get("id") or f"amz_{asin}_{len(comments)}")
        author_el = rv.select_one(".a-profile-name")
        author = author_el.get_text(" ", strip=True) if author_el else "Amazon Customer"

        rating_el = rv.select_one(
            '[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"], i.a-icon-star'
        )
        rating = clamp_rating(rating_el.get_text(" ", strip=True) if rating_el else None)

        title_el = rv.select_one('[data-hook="reviewTitle"], [data-hook="review-title"]')
        title_txt = ""
        if title_el:
            # Exclude nested "5.0 out of 5 stars" span if present inside legacy review-title
            for star_span in title_el.select("i.a-icon-star, span.a-icon-alt"):
                star_span.decompose()
            title_txt = title_el.get_text(" ", strip=True)

        date_el = rv.select_one('[data-hook="review-date"]')
        date_txt = _clean_review_date(date_el.get_text(" ", strip=True) if date_el else "")

        body_el = rv.select_one(
            '[data-hook="reviewText"], [data-hook="review-body"], [data-hook="review-collapsed"], .review-text-content'
        )
        body_txt = body_el.get_text(" ", strip=True) if body_el else ""
        if not body_txt:
            body_txt = title_txt
        elif title_txt and title_txt.lower() not in body_txt.lower()[: len(title_txt) + 10]:
            body_txt = f"{title_txt} — {body_txt}"

        append_unique_comment(
            comments=comments,
            seen_keys=seen_keys,
            comment_id=f"amz_{asin}_{rev_id}",
            author=author,
            text=body_txt,
            published_date=date_txt,
            rating=rating,
            platform="Amazon",
            source=source,
        )

    # Fallback: JSON-LD Product review nodes if present
    if not comments:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "{}")
            except Exception:
                continue
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                for idx, r in enumerate(node.get("review") or []):
                    if not isinstance(r, dict):
                        continue
                    auth = r.get("author")
                    auth_name = auth.get("name") if isinstance(auth, dict) else str(auth or "Amazon Customer")
                    rr = r.get("reviewRating") or {}
                    r_val = rr.get("ratingValue") if isinstance(rr, dict) else None
                    body = str(r.get("reviewBody") or r.get("description") or "").strip()
                    append_unique_comment(
                        comments=comments,
                        seen_keys=seen_keys,
                        comment_id=f"amz_ld_{asin}_{idx}",
                        author=auth_name,
                        text=body,
                        published_date=r.get("datePublished") or "",
                        rating=r_val,
                        platform="Amazon",
                        source=source,
                    )

    has_next = bool(soup.select_one("li.a-last:not(.a-disabled) a"))
    return {
        "comments": comments,
        "total_reported": total_reported,
        "signin_walled": signin_walled and len(comments) == 0,
        "captcha_walled": captcha_walled and len(comments) == 0,
        "has_next": has_next,
    }


def _collect_amazon_sync(
    source: DiscoveredSource,
    asin: str | None,
    max_items: int | None,
) -> dict[str, Any]:
    """Fetch Amazon reviews synchronously across product-reviews pagination & /dp/ page."""
    cookies = settings.amazon_cookie.strip() if settings.has_amazon_cookie else ""
    sess = create_chrome_session(cookies=cookies)
    if sess is None:
        return {"comments": [], "total_reported": 0, "signin_walled": False, "captcha_walled": False}

    comments: list[RawComment] = []
    seen_keys: set[str] = set()
    total_reported = 0
    signin_walled = False
    captcha_walled = False

    headers = {"Accept-Language": "en-US,en;q=0.9"}

    try:
        # Resolve shortlinks (amzn.to / amzn.in / a.co) and inspect initial URL
        resp = sess.get(source.url, headers=headers)
        final_url = str(getattr(resp, "url", source.url))
        resolved_asin = asin or _extract_asin(final_url) or "item"
        host = urlparse(final_url).netloc or urlparse(source.url).netloc or "www.amazon.com"
        if any(tok in host.lower() for tok in ("amzn.", "a.co")):
            host = "www.amazon.com"

        def _merge(batch_res: dict[str, Any]) -> int:
            nonlocal total_reported, signin_walled, captcha_walled
            total_reported = max(total_reported, batch_res["total_reported"])
            signin_walled = signin_walled or batch_res["signin_walled"]
            captcha_walled = captcha_walled or batch_res["captcha_walled"]
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
                    platform="Amazon",
                    source=source,
                ):
                    added += 1
            return added

        # If authenticated cookie or source is already a working /product-reviews/ page, paginate cleanly
        if resolved_asin != "item" and (cookies or "/product-reviews/" in final_url):
            max_pages = 5000 if max_items is None else max(200, (max_items // 8) + 20)
            for page_num in range(1, max_pages + 1):
                if max_items is not None and len(comments) >= max_items:
                    break
                if page_num == 1 and "/product-reviews/" in final_url and resp.status_code == 200:
                    page_html = resp.text
                    page_final = final_url
                else:
                    pr_url = (
                        f"https://{host}/product-reviews/{resolved_asin}/"
                        f"?ie=UTF8&reviewerType=all_reviews&sortBy=recent&pageNumber={page_num}"
                    )
                    pr_resp = sess.get(pr_url, headers=headers)
                    page_html = pr_resp.text if pr_resp.status_code == 200 else ""
                    page_final = str(getattr(pr_resp, "url", pr_url))

                if "/ap/signin" in page_final or not page_html:
                    signin_walled = True
                    break

                parsed = _parse_amazon_reviews_html(page_html, resolved_asin, source)
                added = _merge(parsed)
                if added == 0 or not parsed["has_next"]:
                    break
                time.sleep(0.3)

        # Extract embedded reviews from initial product page (or fallback to /dp/{asin} if source hit /ap/signin)
        if resp.status_code == 200 and "/ap/signin" not in final_url:
            _merge(_parse_amazon_reviews_html(resp.text, resolved_asin, source))

        if (max_items is None or len(comments) < max_items) and resolved_asin != "item" and "/dp/" not in final_url:
            dp_url = f"https://{host}/dp/{resolved_asin}"
            dp_resp = sess.get(dp_url, headers=headers)
            if dp_resp.status_code == 200 and "/ap/signin" not in str(getattr(dp_resp, "url", dp_url)):
                _merge(_parse_amazon_reviews_html(dp_resp.text, resolved_asin, source))

    except Exception as e:
        logger.warning(f"Amazon scrape failed for {source.url}: {e}")
    finally:
        try:
            sess.close()
        except Exception:
            pass

    return {
        "comments": comments,
        "total_reported": total_reported,
        "signin_walled": signin_walled,
        "captcha_walled": captcha_walled,
    }


async def collect_amazon(source: DiscoveredSource) -> tuple[list[RawComment], DiscoveredSource]:
    """Collect customer reviews from Amazon URLs with optional Apify/cookie full pagination."""
    asin = _extract_asin(source.url)
    host = urlparse(source.url).netloc.lower()
    max_items: int | None = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
    auth_mode = 2 if settings.has_apify else (1 if settings.has_amazon_cookie else 0)
    cache_id = f"amazon_v2::{host}::{asin or source.url}::{auth_mode}::{settings.max_comments_per_url}"

    cache = get_cache()
    cached = cache.get("s3_amazon", cache_id)
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
                    "collector_used": cached.get("collector_used", "amazon (cached)"),
                    "error_message": cached.get("error_message", ""),
                }
            )
            return restored, updated

    # Layer 1: Optional Apify full review scraper
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
                "s3_amazon",
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
    res = await loop.run_in_executor(None, _collect_amazon_sync, source, asin, max_items)
    comments: list[RawComment] = res["comments"]
    total_reported: int = res["total_reported"]
    signin_walled: bool = res["signin_walled"]
    captcha_walled: bool = res["captcha_walled"]

    if max_items is not None and len(comments) > max_items:
        comments = comments[:max_items]

    collector_name = "amazon_cookie_reviews" if settings.has_amazon_cookie else "amazon_dp_html"

    if not comments:
        if captcha_walled:
            msg = "Blocked: Amazon presented an anti-bot CAPTCHA; add APIFY_API_KEY or retry later"
        elif signin_walled or settings.has_amazon_cookie:
            msg = "No public reviews visible on this Amazon page (add/refresh AMAZON_COOKIE or APIFY_API_KEY for /product-reviews/)"
        else:
            msg = "Amazon hid public reviews on this region/product page without login (add AMAZON_COOKIE or APIFY_API_KEY in Optional Settings)"
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
            f"Collected {len(comments)} of ~{total_reported:,} ratings/reviews "
            "(Amazon requires sign-in for full /product-reviews/ pagination; add AMAZON_COOKIE or APIFY_API_KEY in Optional Settings)"
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
        "s3_amazon",
        cache_id,
        {
            "comments": [c.model_dump() for c in comments],
            "scrape_status": updated_source.scrape_status,
            "collector_used": updated_source.collector_used,
            "error_message": updated_source.error_message,
        },
    )
    return comments, updated_source
