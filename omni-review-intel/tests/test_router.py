"""
tests/test_router.py — Unit tests for the Stage 3 URL router and collectors.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.schemas import DiscoveredSource, RawComment
from stages.s2_web_discovery import (
    _strip_tracking,
    _canonical,
    _is_junk,
    _detect_platform,
)
from stages.s1_input_analyzer import classify_url as s1_classify


class TestUrlClassification:
    def test_youtube_classified_as_video(self):
        assert s1_classify("https://www.youtube.com/watch?v=abc123") == "video"
        assert s1_classify("https://youtu.be/abc123") == "video"

    def test_instagram_classified_as_video(self):
        assert s1_classify("https://www.instagram.com/reel/abc") == "video"

    def test_image_url_classified_as_image(self):
        assert s1_classify("https://cdn.example.com/product.jpg") == "image"
        assert s1_classify("https://cdn.example.com/image.webp?v=1") == "image"

    def test_amazon_product_classified(self):
        assert s1_classify("https://www.amazon.com/dp/B09XYZ") == "product"

    def test_generic_article(self):
        assert s1_classify("https://techcrunch.com/2024/review-of-gadget") == "article"


class TestUrlUtilities:
    def test_strips_utm_params(self):
        url = "https://example.com/page?utm_source=google&utm_medium=cpc&id=123"
        clean = _strip_tracking(url)
        assert "utm_source" not in clean
        assert "id=123" in clean

    def test_canonical_lowercases(self):
        assert _canonical("https://Example.COM/Page") == _canonical("https://example.com/page")

    def test_junk_filter(self):
        assert _is_junk("https://example.com/login")
        assert _is_junk("https://example.com/privacy-policy")
        assert not _is_junk("https://reddit.com/r/gadgets")

    def test_platform_detection(self):
        assert _detect_platform("https://youtube.com/watch?v=x") == "YouTube"
        assert _detect_platform("https://reddit.com/r/x") == "Reddit"
        assert _detect_platform("https://amazon.com/dp/x") == "Amazon"
        assert _detect_platform("https://randomsite.com") == "Other"
        assert _detect_platform("https://www.amazon.com/product-reviews/B09XYZ") == "Amazon"
        assert _detect_platform("https://www.amazon.com/dp/B09XYZ1234?ref_=nav_custrec_signin") == "Amazon"
        assert _detect_platform("https://www.flipkart.com/") == "Flipkart"
        assert _detect_platform("https://www.flipkart.com/product-reviews/B09XYZ") == "Flipkart"
        assert _detect_platform("https://www.flipkart.com/dp/B09XYZ1234?ref_=nav_custrec_signin") == "Flipkart"
        assert _detect_platform("https://www.instagram.com/p/C0000000000/") == "Instagram"
        assert _detect_platform("https://www.instagram.com/p/C0000000000/reel/") == "Instagram"
        assert _detect_platform("https://www.instagram.com/reel/C0000000000/") == "Instagram"
        assert _detect_platform("https://twitter.com/user/status/123456") == "Twitter"
        assert _detect_platform("https://x.com/user/status/123456") == "Twitter"



class TestRouter:
    @pytest.mark.asyncio
    async def test_routes_youtube(self):
        from stages.s3_collectors.router import _collect_one
        import asyncio

        source = DiscoveredSource(url="https://youtube.com/watch?v=test", platform="YouTube")

        mock_comments = [
            RawComment(source_url=source.url, platform="YouTube", original_text="Nice!")
        ]

        with patch(
            "stages.s3_collectors.router.collect_youtube",
            new=AsyncMock(return_value=(mock_comments, source)),
        ):
            sem = asyncio.Semaphore(1)
            comments, updated = await _collect_one(source, sem)
            assert len(comments) == 1
            assert comments[0].platform == "YouTube"

    @pytest.mark.asyncio
    async def test_handles_collector_exception_gracefully(self):
        from stages.s3_collectors.router import _collect_one
        import asyncio

        source = DiscoveredSource(url="https://youtube.com/watch?v=fail", platform="YouTube")

        with patch(
            "stages.s3_collectors.router.collect_youtube",
            new=AsyncMock(side_effect=RuntimeError("API down")),
        ):
            sem = asyncio.Semaphore(1)
            comments, updated = await _collect_one(source, sem)
            assert comments == []
            assert updated.scrape_status == "failed"
            assert "API down" in updated.error_message

    def test_uncapped_limit_helper(self):
        from config import settings
        orig = settings.max_comments_per_url
        try:
            settings.max_comments_per_url = 0
            limit = settings.max_comments_per_url if settings.max_comments_per_url > 0 else None
            items = list(range(2500))
            assert len(items[:limit]) == 2500  # Not 0!
        finally:
            settings.max_comments_per_url = orig


def test_router_instagram_not_skipped_to_apify():
    import asyncio
    from unittest.mock import AsyncMock, patch
    from core.schemas import DiscoveredSource, RawComment
    from stages.s3_collectors.router import _APIFY_PLATFORMS, _IG_PLATFORMS, _collect_one

    assert "Instagram" not in _APIFY_PLATFORMS
    assert "Instagram" in _IG_PLATFORMS

    src = DiscoveredSource(
        url="https://www.instagram.com/reel/DgLXfvcp_dY/",
        domain="instagram.com",
        platform="Instagram",
        title="BBC News Post",
        is_target=True,
    )
    comment = RawComment(
        comment_id="ig_DgLXfvcp_dY_1",
        source_url=src.url,
        platform="Instagram",
        author="reader1",
        published_date="2026-04-01",
        is_target=True,
        original_text="Incredible achievement!",
    )
    updated = src.model_copy(
        update={
            "scrape_status": "partial",
            "collector_used": "instagram_public_html",
            "comments_collected": 1,
            "error_message": "Collected 1 of ~964 comments",
        }
    )

    async def _run():
        sem = asyncio.Semaphore(1)
        with patch("stages.s3_collectors.router.collect_instagram", new=AsyncMock(return_value=([comment], updated))) as mock_ig:
            return await _collect_one(src, sem), mock_ig.await_count

    (res_comments, res_src), awaited = asyncio.run(_run())
    assert awaited == 1
    assert len(res_comments) == 1
    assert res_src.scrape_status == "partial"
    assert res_src.collector_used == "instagram_public_html"


def test_effective_max_comments_and_router_cancel_timeout(monkeypatch):
    import asyncio
    import threading
    import config
    from stages.s3_collectors.router import _collect_one

    orig = config.settings.max_comments_per_url
    try:
        config.settings.max_comments_per_url = 0
        monkeypatch.setattr(config, "get_container_memory_limit_mb", lambda: None)
        assert config.effective_max_comments(is_target=True) == 0
        assert config.effective_max_comments(is_target=False) == 1500

        monkeypatch.setattr(config, "get_container_memory_limit_mb", lambda: 1024.0)
        assert config.effective_max_comments(is_target=True) == 0
        assert config.effective_max_comments(is_target=False) == 1000
    finally:
        config.settings.max_comments_per_url = orig

    ev = threading.Event()
    ev.set()
    src = DiscoveredSource(url="https://youtube.com/watch?v=test", platform="YouTube", is_target=True)
    with config.scoped_cancel_event(ev):
        c_list, out_src = asyncio.run(_collect_one(src, asyncio.Semaphore(1)))
    assert c_list == []
    assert out_src.scrape_status == "skipped"
    assert "cancelled" in out_src.error_message.lower()


def test_youtube_tok_key_deduplicates_compactly():
    from stages.s3_collectors.youtube_collector import _extract_innertube_page, _tok_key

    visited = set()
    replies = set()
    payload = {
        "onResponseReceivedEndpoints": [
            {
                "appendContinuationItemsAction": {
                    "continuationItems": [
                        {
                            "continuationItemRenderer": {
                                "continuationEndpoint": {"continuationCommand": {"token": "tok_next_123"}}
                            }
                        }
                    ]
                }
            }
        ]
    }
    _, _, nxt1, _ = _extract_innertube_page(
        data=payload,
        video_id="k_mx4THsQik",
        source_url="https://youtube.com/watch?v=k_mx4THsQik",
        is_target=True,
        seen_ids=set(),
        max_items=100,
        current_count=0,
        visited_tokens=visited,
        seen_reply_tokens=replies,
    )
    assert nxt1 == "tok_next_123"
    visited.add(_tok_key("tok_next_123"))
    _, _, nxt2, _ = _extract_innertube_page(
        data=payload,
        video_id="k_mx4THsQik",
        source_url="https://youtube.com/watch?v=k_mx4THsQik",
        is_target=True,
        seen_ids=set(),
        max_items=100,
        current_count=0,
        visited_tokens=visited,
        seen_reply_tokens=replies,
    )
    assert nxt2 is None
