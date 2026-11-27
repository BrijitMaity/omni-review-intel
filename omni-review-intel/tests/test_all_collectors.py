"""
Comprehensive unit tests for Amazon, Flipkart, Facebook, TikTok, Twitter/X,
Reddit, and general Website collectors + Stage 1/2/Router/Pipeline fixes.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from core.schemas import DiscoveredSource, RawComment, TopicProfile
from stages.s1_input_analyzer import (
    _clean_flipkart_or_ecommerce_title,
    _extract_title_from_text_or_url,
    classify_url,
)
from stages.s2_web_discovery import _canonical, _detect_platform, _is_junk
from stages.s3_collectors.amazon_collector import (
    _extract_asin,
    _parse_amazon_reviews_html,
    collect_amazon,
)
from stages.s3_collectors.facebook_collector import (
    _extract_facebook_id,
    _parse_facebook_html_data,
    collect_facebook,
)
from stages.s3_collectors.flipkart_collector import (
    _extract_flipkart_item_id,
    _parse_flipkart_reviews_html,
    _to_reviews_page_url,
    collect_flipkart,
)
from stages.s3_collectors.router import _APIFY_PLATFORMS, _collect_one
from stages.s3_collectors.tiktok_collector import _extract_tiktok_video_id, collect_tiktok
from stages.s3_collectors.web_collector import (
    _extract_dom_reviews_or_comments,
    _extract_jsonld_items,
    _heuristic_extract_web_comments,
    collect_web,
)


# ── Amazon ────────────────────────────────────────────────────────────────────


def test_amazon_asin_extraction_and_html_parsing():
    assert _extract_asin("https://www.amazon.in/dp/B09XS7JWHH?th=1") == "B09XS7JWHH"
    assert _extract_asin("https://www.amazon.com/product-reviews/B09XS7JWHH/ref=cm_cr_dp") == "B09XS7JWHH"
    assert _extract_asin("https://www.amazon.co.uk/gp/product/b09xs7jwhh") == "B09XS7JWHH"
    assert _extract_asin("https://flipkart.com/dp/B09XS7JWHH") is None

    html = """
    <html><body>
      <span id="acrCustomerReviewText">1,250 ratings</span>
      <div data-hook="review" id="R111ABC">
        <span class="a-profile-name">Rohan K</span>
        <i data-hook="review-star-rating"><span class="a-icon-alt">5.0 out of 5 stars</span></i>
        <h5 data-hook="reviewTitle">Superb ANC</h5>
        <span data-hook="review-date">Reviewed in India on 4 November 2026</span>
        <div data-hook="reviewText">
          <div class="a-teaser-describedby-collapsed a-hidden">Brief content visible, double tap to read full content.</div>
          Crystal clear audio and very comfortable earcups.
        </div>
      </div>
    </body></html>
    """
    src = DiscoveredSource(url="https://www.amazon.in/dp/B09XS7JWHH", platform="Amazon", is_target=True)
    parsed = _parse_amazon_reviews_html(html, "B09XS7JWHH", src)
    assert parsed["total_reported"] == 1250
    assert len(parsed["comments"]) == 1
    c = parsed["comments"][0]
    assert c.author == "Rohan K"
    assert c.rating == 5.0
    assert c.published_date == "4 November 2026"
    assert "Superb ANC" in c.original_text
    assert "Crystal clear audio" in c.original_text
    assert "double tap to read full content" not in c.original_text


def test_amazon_collector_returns_partial_without_apify_or_cookie():
    src = DiscoveredSource(url="https://www.amazon.in/dp/B09XS7JWHH", platform="Amazon", is_target=True)
    c1 = RawComment(
        comment_id="amz_B09XS7JWHH_R1",
        source_url=src.url,
        platform="Amazon",
        author="User 1",
        published_date="2026-11-01",
        rating=4.5,
        is_target=True,
        original_text="Great sound quality and battery life!",
    )
    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    with (
        patch("stages.s3_collectors.amazon_collector.get_cache", return_value=mock_cache),
        patch("stages.s3_collectors.amazon_collector.settings.apify_api_key", ""),
        patch("stages.s3_collectors.amazon_collector.settings.amazon_cookie", ""),
        patch("stages.s3_collectors.amazon_collector.settings.max_comments_per_url", 500),
        patch(
            "stages.s3_collectors.amazon_collector._collect_amazon_sync",
            return_value={
                "comments": [c1],
                "total_reported": 1250,
                "signin_walled": True,
                "captcha_walled": False,
            },
        ),
    ):
        comments, updated = asyncio.run(collect_amazon(src))

    assert len(comments) == 1
    assert updated.scrape_status == "partial"
    assert updated.collector_used == "amazon_dp_html"
    assert "1,250" in updated.error_message


# ── Flipkart ──────────────────────────────────────────────────────────────────


def test_flipkart_url_transform_and_initial_state_parsing():
    prod_url = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4?pid=MOBGTAGPTB3VS24W"
    assert _extract_flipkart_item_id(prod_url) == "itm6ac6485515ae4"
    rev_url = _to_reviews_page_url(prod_url, page=2)
    assert "/product-reviews/" in rev_url
    assert "pid=MOBGTAGPTB3VS24W" in rev_url
    assert "page=2" in rev_url

    state = {
        "multiWidgetState": {
            "widgets": [
                {
                    "totalReviewCount": 420,
                    "value": {
                        "type": "ProductReviewValue",
                        "id": "fk-rev-1",
                        "author": "Khushboo Verma",
                        "rating": 5,
                        "title": "Super!",
                        "text": "Great camera and battery backup.",
                        "created": "Dec, 2024",
                    },
                }
            ]
        }
    }
    html = f"<script>window.__INITIAL_STATE__ = {json.dumps(state)};</script>"
    src = DiscoveredSource(url=prod_url, platform="Flipkart", is_target=True)
    parsed = _parse_flipkart_reviews_html(html, src)
    assert parsed["total_reported"] == 420
    assert len(parsed["comments"]) == 1
    c = parsed["comments"][0]
    assert c.author == "Khushboo Verma"
    assert c.rating == 5.0
    assert "Super!" in c.original_text
    assert "Great camera" in c.original_text


def test_flipkart_collector_success():
    src = DiscoveredSource(
        url="https://www.flipkart.com/apple-iphone-15/p/itm123456?pid=MOB1",
        platform="Flipkart",
        is_target=True,
    )
    fk_comment = RawComment(
        comment_id="fk_1",
        source_url=src.url,
        platform="Flipkart",
        author="Verified Buyer",
        published_date="Dec, 2024",
        rating=5.0,
        is_target=True,
        original_text="Excellent display quality",
    )
    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    with (
        patch("stages.s3_collectors.flipkart_collector.get_cache", return_value=mock_cache),
        patch("stages.s3_collectors.flipkart_collector.settings.max_comments_per_url", 0),
        patch(
            "stages.s3_collectors.flipkart_collector._collect_flipkart_sync",
            return_value={"comments": [fk_comment], "total_reported": 1},
        ),
    ):
        comments, updated = asyncio.run(collect_flipkart(src))

    assert len(comments) == 1
    assert updated.scrape_status == "success"
    assert updated.collector_used == "flipkart_reviews"


# ── Facebook ──────────────────────────────────────────────────────────────────


def test_facebook_id_and_relay_json_parsing():
    assert _extract_facebook_id("https://www.facebook.com/reel/1674391298058189/") == "1674391298058189"
    assert _extract_facebook_id("https://www.facebook.com/watch/?v=99887766") == "99887766"
    assert _extract_facebook_id("https://fb.watch/abcDEF123/") == "abcDEF123"
    assert _extract_facebook_id("https://www.facebook.com/bbcnews/posts/pfbid02U7dP8/") == "pfbid02U7dP8"

    fb_payload = {
        "comment_rendering_instance": {"comments": {"total_count": 122}},
        "comment_node": {
            "__typename": "Comment",
            "legacy_fbid": "1169802642190027",
            "created_time": 1775000000,
            "author": {"name": "Jane Reader"},
            "preferred_body": {"text": "Important reporting from BBC News."},
        },
    }
    html = f'<script type="application/json">{json.dumps(fb_payload)}</script>'
    src = DiscoveredSource(
        url="https://www.facebook.com/bbcnews/posts/pfbid02U7dP8/",
        platform="Facebook",
        is_target=True,
    )
    parsed = _parse_facebook_html_data(html, src)
    assert parsed["total_reported"] == 122
    assert len(parsed["comments"]) == 1
    assert parsed["comments"][0].author == "Jane Reader"
    assert parsed["comments"][0].platform == "Facebook"
    assert "Important reporting" in parsed["comments"][0].original_text


def test_facebook_collector_partial_status():
    src = DiscoveredSource(
        url="https://www.facebook.com/reel/1674391298058189/",
        platform="Facebook",
        is_target=True,
    )
    fb_c = RawComment(
        comment_id="fb_1",
        source_url=src.url,
        platform="Facebook",
        author="FB User",
        published_date="2026-11-25",
        is_target=True,
        original_text="Insightful video report!",
    )
    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    with (
        patch("stages.s3_collectors.facebook_collector.get_cache", return_value=mock_cache),
        patch("stages.s3_collectors.facebook_collector.settings.facebook_cookie", ""),
        patch("stages.s3_collectors.facebook_collector.settings.max_comments_per_url", 500),
        patch(
            "stages.s3_collectors.facebook_collector._collect_facebook_sync",
            return_value={"comments": [fb_c], "total_reported": 122, "login_walled": False},
        ),
    ):
        comments, updated = asyncio.run(collect_facebook(src))

    assert len(comments) == 1
    assert updated.scrape_status == "partial"
    assert "122" in updated.error_message


# ── TikTok, Twitter/X, & General Websites ─────────────────────────────────────


def test_tiktok_id_and_fallback_collection():
    assert _extract_tiktok_video_id("https://www.tiktok.com/@bbcnews/video/7400000000000000001") == "7400000000000000001"
    src = DiscoveredSource(
        url="https://www.tiktok.com/@bbcnews/video/7400000000000000001",
        platform="TikTok",
        is_target=True,
    )
    tt_c = RawComment(
        comment_id="tt_1",
        source_url=src.url,
        platform="TikTok",
        author="tt_user",
        published_date="2026-11-25",
        is_target=True,
        original_text="Love this breakdown!",
    )
    mock_cache = MagicMock()
    mock_cache.get.return_value = None
    with (
        patch("stages.s3_collectors.tiktok_collector.get_cache", return_value=mock_cache),
        patch("stages.s3_collectors.tiktok_collector.settings.apify_api_key", ""),
        patch("stages.s3_collectors.tiktok_collector.settings.max_comments_per_url", 100),
        patch(
            "stages.s3_collectors.tiktok_collector._collect_tiktok_sync",
            return_value={"comments": [tt_c], "total_reported": 1},
        ),
    ):
        comments, updated = asyncio.run(collect_tiktok(src))

    assert len(comments) == 1
    assert updated.scrape_status == "success"
    assert updated.collector_used == "tiktok_comment_api"


def test_web_structured_jsonld_dom_and_uncapped_heuristic():
    ld_data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "review": [
            {
                "@type": "Review",
                "author": {"@type": "Person", "name": "Priya S"},
                "datePublished": "2026-11-10",
                "reviewRating": {"@type": "Rating", "ratingValue": 4},
                "reviewBody": "Very reliable laptop for coding and daily meetings.",
            }
        ],
    }
    html = f"""
    <html>
      <head><script type="application/ld+json">{json.dumps(ld_data)}</script></head>
      <body>
        <article class="comment">
          <span class="comment-author">TechReader99</span>
          <time datetime="2026-11-12">Nov 12</time>
          <div class="comment-content">Battery easily lasted 14 hours in our office workflow test.</div>
        </article>
        <a rel="next" href="/reviews/page/2">Next</a>
      </body>
    </html>
    """
    src = DiscoveredSource(url="https://techblog.example.com/reviews", platform="Blog", is_target=True)
    comments: list[RawComment] = []
    seen: set[str] = set()
    _extract_jsonld_items(html, src, seen, comments, max_items=None)
    next_links = _extract_dom_reviews_or_comments(html, src, seen, comments, max_items=None)

    assert len(comments) == 2
    assert comments[0].author == "Priya S"
    assert comments[0].rating == 4.0
    assert comments[1].author == "TechReader99"
    assert next_links == ["https://techblog.example.com/reviews/page/2"]

    # Ensure heuristic extraction is NOT capped at 50 when uncapped
    many_blocks = "\n\n".join(
        f"Reviewer{i}: This detailed product review #{i} explains real battery and comfort results."
        for i in range(80)
    )
    heuristic = _heuristic_extract_web_comments(many_blocks, src.url, max_items=None)
    assert len(heuristic) == 80


def test_web_twitter_fxtwitter_routing():
    src = DiscoveredSource(
        url="https://x.com/BBCWorld/status/1234567890",
        platform="Twitter",
        is_target=True,
    )
    tw_comment = RawComment(
        comment_id="x_1234567890",
        source_url=src.url,
        platform="Twitter",
        author="@BBCWorld",
        published_date="2026-11-25",
        is_target=True,
        original_text="Breaking news update from London.",
    )
    mock_cache = MagicMock()
    mock_cache.get.return_value = None
    with (
        patch("stages.s3_collectors.web_collector.get_cache", return_value=mock_cache),
        patch("stages.s3_collectors.web_collector._collect_twitter_sync", return_value=[tw_comment]),
    ):
        comments, updated = asyncio.run(collect_web(src))

    assert len(comments) == 1
    assert updated.collector_used == "fxtwitter_api"
    assert updated.scrape_status == "success"


# ── Stage 1 / Stage 2 / Router Fixes ─────────────────────────────────────────


def test_platform_detection_junk_and_router_fixes():
    assert _APIFY_PLATFORMS == set()

    # Facebook detected properly & not mistaken for Other; xbox.com NOT mistaken for Twitter!
    assert _detect_platform("https://www.facebook.com/bbcnews/posts/123") == "Facebook"
    assert _detect_platform("https://fb.watch/abc123/") == "Facebook"
    assert _detect_platform("https://www.xbox.com/en-US/consoles") != "Twitter"
    assert _detect_platform("https://x.com/user/status/123") == "Twitter"
    assert _detect_platform("https://www.trustpilot.com/review/amazon.com") == "ECommerce"
    assert _detect_platform("https://amzn.to/3xyz") == "Amazon"

    # Junk filter allows product slugs containing 'cookie' while blocking real login/privacy pages
    assert not _is_junk("https://bakeryblog.com/oreo-cookie-review/")
    assert _is_junk("https://example.com/login")
    assert _is_junk("https://example.com/privacy-policy")
    assert _is_junk("https://en.wikipedia.org/wiki/India")
    assert _is_junk("https://www.worldometers.info/countries/india/")
    assert _is_junk("https://www.india.gov.in/topics/art-culture")
    assert not _is_junk("https://www.techradar.com/reviews/sony-wh-1000xm5")


def test_http_utils_fetch_falls_back_to_httpx_on_429():
    from stages.s3_collectors._http_utils import fetch_url_sync

    fake_curl_resp = MagicMock(status_code=429, text="rate limited", url="https://www.google.com/sorry/index")
    fake_curl_sess = MagicMock()
    fake_curl_sess.get.return_value = fake_curl_resp

    fake_httpx_resp = MagicMock(status_code=200, text="<html><body>OK</body></html>", url="https://example.com/ok")
    fake_httpx_client = MagicMock()
    fake_httpx_client.__enter__.return_value = fake_httpx_client
    fake_httpx_client.get.return_value = fake_httpx_resp

    with (
        patch("stages.s3_collectors._http_utils.create_chrome_session", return_value=fake_curl_sess),
        patch("stages.s3_collectors._http_utils.httpx.Client", return_value=fake_httpx_client),
    ):
        status, text, final_url = fetch_url_sync("https://example.com/ok")

    assert status == 200
    assert "OK" in text
    assert final_url == "https://example.com/ok"


def test_resilient_session_fails_over_curl_429_to_httpx():
    from stages.s3_collectors._http_utils import create_chrome_session

    fake_primary = MagicMock()
    fake_primary.cookies = {"CONSENT": "YES+cb"}
    fake_primary.get.return_value = MagicMock(
        status_code=429, text="rate limited", url="https://www.google.com/sorry/index"
    )

    fake_httpx_resp = MagicMock(status_code=200, text="<html>Ok</html>", url="https://example.com/p")
    fake_httpx_client = MagicMock()
    fake_httpx_client.__enter__.return_value = fake_httpx_client
    fake_httpx_client.get.return_value = fake_httpx_resp

    with (
        patch("stages.s3_collectors._http_utils.curl_requests.Session", return_value=fake_primary),
        patch("stages.s3_collectors._http_utils.httpx.Client", return_value=fake_httpx_client),
    ):
        sess = create_chrome_session()
        resp = sess.get("https://example.com/p")
        sess.close()

    assert resp.status_code == 200
    assert "Ok" in resp.text


def test_uncapped_flipkart_scales_beyond_old_100_page_cap():
    from config import settings
    from stages.s3_collectors.flipkart_collector import _collect_flipkart_sync

    src = DiscoveredSource(
        url="https://www.flipkart.com/item/product-reviews/itm6ac6485515ae4",
        platform="Flipkart",
        is_target=True,
    )
    requested_urls = []

    class _FakeResp:
        def __init__(self, status_code, text, url):
            self.status_code = status_code
            self.text = text
            self.url = url

    def _fake_get(url, headers=None):
        requested_urls.append(url)
        # Return 1 unique review per page up to page 105 (beyond old 100-page cap) then empty on page 106+
        m = __import__("re").search(r"page=(\d+)", url)
        pg = int(m.group(1)) if m else 1
        if pg <= 105:
            payload = {
                "id": f"fk_{pg}",
                "text": f"Detailed genuine product review page {pg}",
                "author": f"User{pg}",
                "rating": 5,
                "created": "2026-11-01",
                "totalReviewCount": 5000,
                "type": "ProductReviewValue",
            }
            html = f"<script>window.__INITIAL_STATE__ = {json.dumps({'node': payload})};</script>"
            return _FakeResp(200, html, url)
        return _FakeResp(200, "<html></html>", url)

    fake_sess = MagicMock()
    fake_sess.get.side_effect = _fake_get

    orig = settings.max_comments_per_url
    try:
        settings.max_comments_per_url = 0
        with patch("stages.s3_collectors.flipkart_collector.create_chrome_session", return_value=fake_sess):
            res = _collect_flipkart_sync(src, max_items=None)
    finally:
        settings.max_comments_per_url = orig

    assert len(res["comments"]) == 105
    assert any("page=105" in u for u in requested_urls)


def test_stage2_filters_off_topic_zhihu_baidu_results():
    from stages.s2_web_discovery import _is_junk, _is_relevant_result, _topic_keywords

    assert _is_junk("https://www.zhihu.com/question/293385672")
    assert _is_junk("https://jingyan.baidu.com/article/c85b7a64.html")
    assert _is_junk("https://forums.getpaint.net/profile/142322-briana")

    topic = TopicProfile(
        canonical_name="Cognizant IS HIRING Analyst Service Desk",
        brand="Cognizant",
        input_type="video",
        search_queries=["Cognizant review", "Cognizant hiring reddit", "Cognizant complaints"],
    )
    kw = _topic_keywords(topic)
    assert not _is_relevant_result("https://example.com/office", "Activate Office 365", "Windows guide", kw)
    assert _is_relevant_result("https://job4freshers.co.in/cognizant", "Cognizant Hiring Freshers", "Service Desk Analyst", kw)

    # Classify URL & Stage 1 title cleaning
    assert classify_url("https://www.facebook.com/watch/?v=123") == "video"
    assert classify_url("https://amzn.to/3xyz") == "product"
    assert "Flipkart" not in _clean_flipkart_or_ecommerce_title("Apple iPhone 15 | Flipkart.com")

    # Canonical dedup across Amazon/Flipkart/Facebook
    assert _canonical("https://www.amazon.in/dp/B09XS7JWHH?ref=abc") == "amazon:amazon.in:B09XS7JWHH"
    assert _canonical("https://www.flipkart.com/iphone/p/itm6ac6485515ae4?utm_source=x") == "flipkart:itm6ac6485515ae4"
    assert _canonical("https://www.facebook.com/reel/1674391298058189/?fbclid=123") == "facebook:1674391298058189"


def test_router_routes_amazon_flipkart_facebook_without_apify():
    async def _run():
        sem = asyncio.Semaphore(1)
        amz = DiscoveredSource(url="https://amazon.in/dp/B09XS7JWHH", platform="Amazon", is_target=True)
        fk = DiscoveredSource(url="https://flipkart.com/item/p/itm1", platform="Flipkart", is_target=True)
        fb = DiscoveredSource(url="https://facebook.com/reel/123", platform="Facebook", is_target=True)

        fake = lambda p: [RawComment(source_url="https://example.com", platform=p, original_text="Good product")]
        with (
            patch("stages.s3_collectors.router.collect_amazon", new=AsyncMock(return_value=(fake("Amazon"), amz))) as m_amz,
            patch("stages.s3_collectors.router.collect_flipkart", new=AsyncMock(return_value=(fake("Flipkart"), fk))) as m_fk,
            patch("stages.s3_collectors.router.collect_facebook", new=AsyncMock(return_value=(fake("Facebook"), fb))) as m_fb,
        ):
            r1 = await _collect_one(amz, sem)
            r2 = await _collect_one(fk, sem)
            r3 = await _collect_one(fb, sem)
            assert m_amz.await_count == 1
            assert m_fk.await_count == 1
            assert m_fb.await_count == 1
            return r1, r2, r3

    r1, r2, r3 = asyncio.run(_run())
    assert r1[0][0].platform == "Amazon"
    assert r2[0][0].platform == "Flipkart"
    assert r3[0][0].platform == "Facebook"



def test_twitter_vxtwitter_fallback():
    """Verify _collect_twitter_sync falls back to api.vxtwitter.com when fxtwitter and syndication fail."""
    from types import SimpleNamespace
    from core.schemas import DiscoveredSource
    from stages.s3_collectors.web_collector import _collect_twitter_sync

    src = DiscoveredSource(url="https://x.com/NASA/status/1234567890", platform="Twitter", title="Tweet", snippet="")

    class _FakeSess:
        def __init__(self):
            self.calls = []
            self.closed = False

        def get(self, url, **kwargs):
            self.calls.append(url)
            if "api.vxtwitter.com" in url:
                payload = {
                    "text": "Spacecraft telemetry nominal!",
                    "user_screen_name": "NASA",
                    "date_epoch": 1700000000,
                    "qrt": {
                        "tweetID": "999",
                        "user_screen_name": "JPL",
                        "text": "Confirmed launch sequence.",
                        "date_epoch": 1699999000,
                    },
                }
                return SimpleNamespace(status_code=200, text=json.dumps(payload), json=lambda: payload)
            return SimpleNamespace(status_code=404, text="{}", json=lambda: {})

        def close(self):
            self.closed = True

    fake = _FakeSess()
    with patch("stages.s3_collectors.web_collector.create_chrome_session", return_value=fake):
        out = _collect_twitter_sync(src, max_items=10)

    assert fake.closed is True
    assert len(fake.calls) == 3
    assert len(out) == 2
    assert out[0].author == "@NASA"
    assert "telemetry nominal" in out[0].original_text
    assert out[1].author == "@JPL"
    assert "Confirmed launch sequence." in out[1].original_text


def test_resilient_session_del_and_verified_tls():
    """Ensure _ResilientSession.__del__ closes primary client and no stage disables aiohttp TLS verification."""
    from pathlib import Path
    from stages.s3_collectors._http_utils import _ResilientSession

    rs = _ResilientSession.__new__(_ResilientSession)
    closed = {"done": False}

    class _P:
        def close(self):
            closed["done"] = True

    rs._primary = _P()
    rs.__del__()
    assert closed["done"] is True

    stages_dir = Path(__file__).resolve().parents[1] / "stages"
    for py_file in stages_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "ssl=False" not in content, f"Insecure TLS ssl=False found in {py_file}"



def test_discovery_and_web_skip_corporate_landings_and_sso():
    """Corporate homepages and SSO login pages must never become fake Community User reviews."""
    from unittest.mock import AsyncMock
    from core.schemas import DiscoveredSource
    from stages.s2_web_discovery import _is_non_review_landing
    from stages.s3_collectors.web_collector import collect_web

    assert _is_non_review_landing("https://onecsit.cognizant.com/Welcome", "Sign in to your account - Cognizant") is True
    assert _is_non_review_landing("https://www.cognizant.com/in/en", "AI Builders Bridging the Gap | Cognizant India") is True
    assert _is_non_review_landing("https://careers.cognizant.com/india-en", "The future is yours to build - Careers at Cognizant") is True
    assert _is_non_review_landing("https://www.ambitionbox.com/reviews/cognizant-reviews", "Cognizant Reviews") is False

    corp_html = """
    <html><body>
      <h2>The future is yours to build in enterprise engineering</h2>
      <p>AMF1 and Cognizant are building together across global cloud delivery centers.</p>
      <p>Cognizant helps companies modernize technology and transform customer experiences.</p>
    </body></html>
    """
    corp_src = DiscoveredSource(
        url="https://www.cognizant.com/in/en",
        platform="Other",
        title="Cognizant India",
        is_target=False,
    )

    with patch("stages.s3_collectors.web_collector._fetch_html_and_markdown", AsyncMock(return_value=(corp_html, "The future is yours to build\n\nAMF1 and Cognizant are building together."))):
        cmts, updated = asyncio.run(collect_web(corp_src))

    assert cmts == []
    assert updated.scrape_status == "failed"


def test_reddit_public_html_shreddit_fallback(tmp_path):
    """Verify collect_reddit falls back to <shreddit-comment> HTML when Reddit .json returns 403."""
    from types import SimpleNamespace
    from core.cache import Cache
    from core.schemas import DiscoveredSource
    from stages.s3_collectors.reddit_collector import collect_reddit

    src = DiscoveredSource(
        url="https://www.reddit.com/r/headphones/comments/w65b33/sony_wh1000xm5_review/",
        platform="Reddit",
        title="Sony WH-1000XM5 Review",
        is_target=True,
    )
    html_payload = """
    <html><body>
      <shreddit-comment thingid="t1_abc" author="audiophile99">
        <faceplate-timeago ts="2025-04-01T10:00:00Z"></faceplate-timeago>
        <div slot="comment"><p>Best ANC for flights, zero complaints so far.</p></div>
      </shreddit-comment>
      <shreddit-comment thingid="t1_def" author="deleted_user">
        <div slot="comment"><p>[removed]</p></div>
      </shreddit-comment>
    </body></html>
    """

    class _FakeSess:
        def get(self, url, **kwargs):
            if url.endswith(".json?limit=500&raw_json=1") or ".json" in url:
                return SimpleNamespace(status_code=403, text="blocked", json=lambda: {})
            return SimpleNamespace(status_code=200, text=html_payload)

        def close(self):
            pass

    fresh_cache = Cache(db_path=str(tmp_path / "reddit_test.db"))

    async def _run():
        with (
            patch("stages.s3_collectors.reddit_collector.get_cache", return_value=fresh_cache),
            patch("stages.s3_collectors.reddit_collector.settings.reddit_client_id", ""),
            patch("stages.s3_collectors.reddit_collector.settings.reddit_client_secret", ""),
            patch("stages.s3_collectors._http_utils.create_chrome_session", return_value=_FakeSess()),
        ):
            return await collect_reddit(src)

    cmts, updated = asyncio.run(_run())
    assert len(cmts) == 1
    assert cmts[0].author == "audiophile99"
    assert "Best ANC for flights" in cmts[0].original_text
    assert cmts[0].published_date.startswith("2025-04-01")
    assert updated.scrape_status == "success"
    assert updated.collector_used == "reddit_public_html"


def test_stage2_youtube_review_fallback_discovers_relevant_videos():
    """When web search adapters return empty/junk, Stage 2 discovers YouTube review videos cleanly."""
    from types import SimpleNamespace
    from stages.s2_web_discovery import _search_youtube_reviews

    fake_payload = {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "itemSectionRenderer": {
                        "contents": [
                            {
                                "videoRenderer": {
                                    "videoId": "6CsJZxfZsL0",
                                    "title": {"runs": [{"text": "Sony WH-1000XM5 Review"}]},
                                    "ownerText": {"runs": [{"text": "Marques Brownlee"}]},
                                }
                            }
                        ]
                    }
                }
            }
        }
    }
    html = f"<script>var ytInitialData = {json.dumps(fake_payload)};</script>"

    class _FakeSess:
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=200, text=html)

        def close(self):
            pass

    with patch("stages.s3_collectors._http_utils.create_chrome_session", return_value=_FakeSess()):
        res = asyncio.run(_search_youtube_reviews("Sony WH-1000XM5 review", max_results=3))

    assert len(res) == 1
    assert res[0]["url"] == "https://www.youtube.com/watch?v=6CsJZxfZsL0"
    assert "Sony WH-1000XM5 Review" in res[0]["title"]
    assert "Marques Brownlee" in res[0]["snippet"]
