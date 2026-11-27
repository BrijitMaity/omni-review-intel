"""
tests/test_s1_input_analyzer.py — Unit tests for Stage 1 URL classification and extraction.
"""

import pytest
from unittest.mock import AsyncMock, patch
from stages.s1_input_analyzer import (
    classify_url,
    _check_content_type,
    _extract_video_metadata,
    _crawl_page_markdown,
)


class TestClassifyUrl:
    def test_youtube_urls(self):
        assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "video"
        assert classify_url("https://youtu.be/dQw4w9WgXcQ") == "video"
        assert classify_url("https://www.youtube.com/shorts/abcdef12345") == "video"

    def test_social_video_urls(self):
        assert classify_url("https://www.instagram.com/reel/C123456/") == "video"
        assert classify_url("https://www.tiktok.com/@user/video/123456789") == "video"

    def test_image_extensions(self):
        assert classify_url("https://example.com/photos/item.png") == "image"
        assert classify_url("https://example.com/photos/item.jpg?quality=high") == "image"
        assert classify_url("https://example.com/photos/item.webp") == "image"

    def test_ecommerce_product_patterns(self):
        assert classify_url("https://www.amazon.com/dp/B09V3KXJPB") == "product"
        assert classify_url("https://amzn.to/3xyz789") == "product"
        assert classify_url("https://www.flipkart.com/apple-iphone-15/p/itm123") == "product"
        assert classify_url("https://www.walmart.com/ip/wireless-headphones") == "product"
        assert classify_url("https://store.example.com/product/laptop-pro") == "product"
        assert classify_url("https://shop.example.com/item/12345") == "product"

    def test_fallback_article(self):
        assert classify_url("https://theverge.com/reviews/gadget-review-2024") == "article"
        assert classify_url("https://medium.com/@author/my-thoughts-on-tech") == "article"


class TestExtractionFallbacks:
    @pytest.mark.asyncio
    async def test_extract_video_metadata_handles_error(self):
        with patch("stages.s1_input_analyzer._YT_DLP_AVAILABLE", False):
            meta = await _extract_video_metadata("https://invalid-url.com")
            assert "title" in meta
            assert "description" in meta

    @pytest.mark.asyncio
    async def test_check_content_type_handles_network_error(self):
        with patch("aiohttp.ClientSession.head", side_effect=Exception("Connection refused")):
            ct = await _check_content_type("https://127.0.0.1:9999/nonexistent", timeout=1)
            assert ct == ""

    @pytest.mark.asyncio
    async def test_crawl_page_markdown_fallback(self):
        with patch("stages.s1_input_analyzer._scrape_html_fallback", new=AsyncMock(return_value="Scraped text")):
            res = await _crawl_page_markdown("https://example.com/test")
            assert "Scraped text" in res

    @pytest.mark.asyncio
    async def test_analyze_input_normalizes_url(self):
        from stages.s1_input_analyzer import analyze_input
        with patch("stages.s1_input_analyzer._extract_video_metadata", new=AsyncMock(return_value={"title": "Test Video"})):
            topic, _ = await analyze_input("youtu.be/dQw4w9WgXcQ")
            assert topic.canonical_name != ""

    def test_fallback_topic_profile_ignores_query_param_brand_collisions(self):
        from stages.s1_input_analyzer import _fallback_topic_profile
        topic = _fallback_topic_profile(
            "Title: INDIA'S GOT LATENT S2 EP7\nUploader: Samay Raina",
            "video",
            "https://youtu.be/rkKZIMPecRA?si=FakeTokenHp999",
        )
        assert topic.canonical_name == "INDIA'S GOT LATENT S2 EP7"
        assert topic.brand == "Samay Raina"  # must NOT misclassify as HP from ?si= token


def test_instagram_metadata_and_brand_fallback():
    from stages.s1_input_analyzer import _fallback_topic_profile, classify_url

    ig_url = "https://www.instagram.com/reel/DgLXfvcp_dY/"
    assert classify_url(ig_url) == "video"

    content = (
        "Title: Video by bbcnews\n"
        "Uploader: BBC News\n"
        "Description: Puerto Rico's Jennifer Barreto becomes first mother to win Miss Universe 2026.\n"
    )
    profile = _fallback_topic_profile(content, "video", ig_url)
    assert "Jennifer Barreto" in profile.canonical_name
    assert profile.brand == "BBC News"
    assert profile.category == "Video & Social Media"


def test_flipkart_long_title_cleaning_and_concise_search_queries():
    from stages.s1_input_analyzer import _clean_flipkart_or_ecommerce_title, _fallback_topic_profile

    raw = (
        "SONY WH-1000XM5 Wireless Noise Cancellation with AI Noise Reduction Bluetooth "
        "Price in India - Buy SONY WH-1000XM5 Online - SONY : Flipkart.com"
    )
    cleaned = _clean_flipkart_or_ecommerce_title(raw)
    assert "Price in India" not in cleaned
    assert "Flipkart.com" not in cleaned
    assert cleaned.startswith("SONY WH-1000XM5")

    profile = _fallback_topic_profile(f"Title: {cleaned}\n\nContent:\nGreat audio", "product", "https://www.flipkart.com/sony-wh-1000xm5/p/itm123")
    assert all(not q.endswith("with review") for q in profile.search_queries)
    assert any(q.startswith("SONY WH-1000XM5") and q.endswith("review") for q in profile.search_queries)
