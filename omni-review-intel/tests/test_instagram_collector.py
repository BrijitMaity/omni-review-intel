"""
Unit tests for Instagram Stage 3 collector, Stage 1 metadata, and Stage 2/3 routing.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.schemas import DiscoveredSource, RawComment
from stages.s1_input_analyzer import (
    _extract_ig_headline,
    _extract_title_from_text_or_url,
    classify_url,
)
from stages.s2_web_discovery import _canonical, _detect_platform, _strip_tracking
from stages.s3_collectors.instagram_collector import (
    _extract_shortcode,
    _normalize_session_id,
    _parse_public_html_data,
    _parse_v1_comment_list,
    collect_instagram,
)
from stages.s3_collectors.router import _collect_one


def _make_ig_source(url: str = "https://www.instagram.com/p/DgLXfvcp_dY/", is_target: bool = True) -> DiscoveredSource:
    return DiscoveredSource(
        url=url,
        domain="instagram.com",
        platform="Instagram",
        title="Test Instagram Post",
        is_target=is_target,
    )


def test_extract_shortcode_variants():
    assert _extract_shortcode("https://www.instagram.com/p/DgLXfvcp_dY/") == "DgLXfvcp_dY"
    assert _extract_shortcode("https://www.instagram.com/reel/DgLXfvcp_dY/?igsh=xyz") == "DgLXfvcp_dY"
    assert _extract_shortcode("https://instagram.com/reels/DgLXfvcp_dY") == "DgLXfvcp_dY"
    assert _extract_shortcode("https://www.instagram.com/tv/AbCdEf12345/") == "AbCdEf12345"
    assert _extract_shortcode("https://www.instagram.com/bbcnews/reel/DgLXfvcp_dY/") == "DgLXfvcp_dY"
    assert _extract_shortcode("https://www.instagram.com/bbcnews/") is None
    assert _extract_shortcode("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None


def test_normalize_session_id():
    assert _normalize_session_id("  '12345%3Aabc%3A27'  ") == "12345%3Aabc%3A27"
    assert _normalize_session_id("csrftoken=abc; sessionid=999%3Axyz; ds_user_id=1") == "999%3Axyz"
    assert _normalize_session_id("") == ""


def test_parse_public_html_polaris_json():
    payload = {
        "require": [
            [
                "RelayPrefetchedStreamCache",
                "next",
                [],
                [
                    "adp_PolarisPostComments",
                    {
                        "__bbox": {
                            "result": {
                                "data": {
                                    "xig_polaris_media": {
                                        "pk": "3888123456789012345",
                                        "code": "DgLXfvcp_dY",
                                        "comment_count": 964,
                                        "comments_connection": {
                                            "edges": [
                                                {
                                                    "node": {
                                                        "pk": "18000001",
                                                        "text": "Congratulations to Jennifer Barreto!",
                                                        "created_at": 1775000000,
                                                        "user": {"username": "viewer_one"},
                                                    }
                                                },
                                                {
                                                    "node": {
                                                        "pk": "18000002",
                                                        "text": "Historic win for Puerto Rico mothers!",
                                                        "created_at": 1775000100,
                                                        "user": {"username": "viewer_two"},
                                                    }
                                                },
                                            ]
                                        },
                                    }
                                }
                            }
                        }
                    },
                ],
            ]
        ]
    }
    html = (
        '<html><head><meta property="og:title" content="BBC News on Instagram: &quot;Miss Universe&quot;" />'
        '</head><body><script type="application/json">'
        + json.dumps(payload)
        + '</script><script>window._d={"csrf_token":"tok123"};</script></body></html>'
    )
    src = _make_ig_source()
    parsed = _parse_public_html_data(html, "DgLXfvcp_dY", src)
    assert parsed["media_id"] == "3888123456789012345"
    assert parsed["total_reported"] == 964
    assert parsed["csrf_token"] == "tok123"
    assert len(parsed["comments"]) == 2
    assert parsed["comments"][0].author == "viewer_one"
    assert parsed["comments"][0].platform == "Instagram"
    assert parsed["comments"][0].is_target is True
    assert "Congratulations" in parsed["comments"][0].original_text


def test_parse_v1_comment_list_with_replies():
    src = _make_ig_source()
    comments: list[RawComment] = []
    seen: set[str] = set()
    items = [
        {
            "pk": "101",
            "text": "Parent comment text",
            "created_at_utc": 1775000000,
            "user": {"username": "alice"},
            "child_comment_count": 3,
            "preview_child_comments": [
                {
                    "pk": "102",
                    "text": "Reply text inside preview",
                    "created_at_utc": 1775000010,
                    "user": {"username": "bob"},
                }
            ],
        }
    ]
    added, more = _parse_v1_comment_list(items, comments, seen, "DgLXfvcp_dY", src, max_items=None)
    assert added == 2
    assert len(comments) == 2
    assert comments[0].author == "alice"
    assert comments[1].author == "bob"
    assert more == [("101", 3, 1)]


def test_collect_instagram_public_html_partial_without_keys():
    src = _make_ig_source()
    sample_comments = [
        RawComment(
            comment_id=f"ig_DgLXfvcp_dY_{i}",
            source_url=src.url,
            platform="Instagram",
            author=f"user_{i}",
            published_date="2026-04-01",
            is_target=True,
            original_text=f"Great inspiring moment #{i}",
        )
        for i in range(10)
    ]

    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    with (
        patch("stages.s3_collectors.instagram_collector.get_cache", return_value=mock_cache),
        patch("stages.s3_collectors.instagram_collector.settings.apify_api_key", ""),
        patch("stages.s3_collectors.instagram_collector.settings.instagram_session_id", ""),
        patch("stages.s3_collectors.instagram_collector.settings.max_comments_per_url", 500),
        patch(
            "stages.s3_collectors.instagram_collector._fetch_post_html_sync",
            return_value=("<html></html>", src.url, ""),
        ),
        patch(
            "stages.s3_collectors.instagram_collector._parse_public_html_data",
            return_value={
                "comments": sample_comments,
                "media_id": "3888123",
                "total_reported": 964,
                "csrf_token": "",
                "login_walled": False,
            },
        ),
    ):
        comments, updated = asyncio.run(collect_instagram(src))

    assert len(comments) == 10
    assert updated.scrape_status == "partial"
    assert updated.collector_used == "instagram_public_html"
    assert updated.comments_collected == 10
    assert "964" in updated.error_message
    assert "INSTAGRAM_SESSION_ID" in updated.error_message


def test_collect_instagram_session_api_full_collection():
    src = _make_ig_source()
    session_comments = [
        RawComment(
            comment_id=f"ig_DgLXfvcp_dY_{i}",
            source_url=src.url,
            platform="Instagram",
            author=f"user_{i}",
            published_date="2026-04-01",
            is_target=True,
            original_text=f"Full paginated comment #{i}",
        )
        for i in range(50)
    ]

    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    with (
        patch("stages.s3_collectors.instagram_collector.get_cache", return_value=mock_cache),
        patch("stages.s3_collectors.instagram_collector.settings.apify_api_key", ""),
        patch("stages.s3_collectors.instagram_collector.settings.instagram_session_id", "12345%3Asession"),
        patch("stages.s3_collectors.instagram_collector.settings.max_comments_per_url", 0),
        patch(
            "stages.s3_collectors.instagram_collector._collect_via_session_api_sync",
            return_value={
                "comments": session_comments,
                "total_reported": 50,
                "session_rejected": False,
            },
        ),
    ):
        comments, updated = asyncio.run(collect_instagram(src))

    assert len(comments) == 50
    assert updated.scrape_status == "success"
    assert updated.collector_used == "instagram_session_api"
    assert updated.error_message == ""


def test_router_routes_instagram_to_collect_instagram():
    src = _make_ig_source()
    fake_comment = RawComment(
        comment_id="ig_1",
        source_url=src.url,
        platform="Instagram",
        author="bbc_fan",
        published_date="2026-04-01",
        is_target=True,
        original_text="Amazing story!",
    )
    updated_src = src.model_copy(update={"scrape_status": "success", "comments_collected": 1, "collector_used": "instagram_public_html"})

    async def _run():
        sem = asyncio.Semaphore(1)
        with patch("stages.s3_collectors.router.collect_instagram", new=AsyncMock(return_value=([fake_comment], updated_src))) as mock_ig:
            res_comments, res_src = await _collect_one(src, sem)
            mock_ig.assert_awaited_once_with(src)
            return res_comments, res_src

    res_comments, res_src = asyncio.run(_run())
    assert len(res_comments) == 1
    assert res_src.collector_used == "instagram_public_html"


def test_stage1_and_stage2_instagram_helpers():
    url = "https://www.instagram.com/reel/DgLXfvcp_dY/?igsh=NTc4MTIwNjQ2YQ=="
    assert classify_url(url) == "video"
    assert _detect_platform(url) == "Instagram"
    assert "igsh=" not in _strip_tracking(url)
    assert _canonical(url) == "instagram:DgLXfvcp_dY"
    assert _canonical("https://www.instagram.com/p/DgLXfvcp_dY/") == "instagram:DgLXfvcp_dY"

    headline, author = _extract_ig_headline(
        'BBC News on Instagram: "Puerto Rico\'s Jennifer Barreto becomes first mother to win Miss Universe"',
        '43K likes, 964 comments - bbcnews on May 1, 2026: "Jennifer Barreto has been crowned Miss Universe 2026"',
    )
    assert author == "BBC News"
    assert "Jennifer Barreto" in headline

    # Generic "Video by ..." title falls back to caption headline
    content = (
        "Title: Video by bbcnews\n"
        "Uploader: bbcnews\n"
        "Description: Puerto Rico's Jennifer Barreto becomes first mother to win Miss Universe 2026.\n"
    )
    extracted = _extract_title_from_text_or_url(content, url)
    assert "Jennifer Barreto" in extracted
    assert not extracted.lower().startswith("video by ")

    # Offline URL-only fallback avoids garbling the shortcode
    assert _extract_title_from_text_or_url("Video URL: " + url, url) == "Instagram Post (DgLXfvcp_dY)"


def test_instagram_extracts_single_emoji_comments_without_false_login_wall():
    from stages.s3_collectors.instagram_collector import _parse_public_html_data

    payload = {
        "require": [[
            "adp_Polaris",
            {
                "__bbox": {
                    "result": {
                        "data": {
                            "xig_polaris_media": {
                                "pk": "4038716507806411681",
                                "code": "DgMZ0QMTquh",
                                "comment_count": 3335,
                                "comments_connection": {
                                    "edges": [
                                        {"node": {"pk": "101", "text": "🙌", "created_at": 1795758146, "user": {"username": "u_one"}}},
                                        {"node": {"pk": "102", "text": "Comment", "created_at": 1795758147, "user": {"username": "u_two"}}},
                                    ]
                                },
                            }
                        }
                    }
                }
            },
        ]]
    }
    html = f'<html><body><a href="/accounts/login/">Log in</a><script type="application/json">{json.dumps(payload)}</script></body></html>'
    src = _make_ig_source("https://www.instagram.com/p/DgMZ0QMTquh/")
    parsed = _parse_public_html_data(html, "DgMZ0QMTquh", src)
    assert len(parsed["comments"]) == 2
    assert parsed["comments"][0].original_text == "🙌"
    assert parsed["login_walled"] is False


def test_instagram_share_link_cookie_parsing_and_shortcode_to_media_id():
    from types import SimpleNamespace
    from stages.s3_collectors.instagram_collector import (
        _fetch_post_html_sync,
        _parse_ig_cookie_dict,
        _resolve_share_shortcode_sync,
        _shortcode_to_media_id,
    )

    assert _shortcode_to_media_id("DgMZ0QMTquh") == "4038716507806411681"

    parsed_full = _parse_ig_cookie_dict("Cookie: csrftoken=abc; ds_user_id=999; sessionid=12345%3Asecret%3A9")
    assert parsed_full["sessionid"] == "12345%3Asecret%3A9"
    assert parsed_full["csrftoken"] == "abc"
    assert parsed_full["ds_user_id"] == "999"

    class _ShareSess:
        def get(self, url, **kwargs):
            return SimpleNamespace(
                status_code=200,
                url="https://www.instagram.com/reel/DgMZ0QMTquh/",
                text="<html></html>",
            )

        def close(self):
            pass

    with patch("stages.s3_collectors.instagram_collector._create_http_session", return_value=_ShareSess()):
        sc, canon = _resolve_share_shortcode_sync("https://www.instagram.com/share/reel/abcXYZ/")
    assert sc == "DgMZ0QMTquh"
    assert "/reel/DgMZ0QMTquh/" in canon

    class _LoginSkipSess:
        def get(self, url, **kwargs):
            if "/p/" in url:
                return SimpleNamespace(
                    status_code=200,
                    url="https://www.instagram.com/accounts/login/?next=...",
                    text="<html>PolarisLoginPage</html>",
                    cookies={},
                )
            return SimpleNamespace(
                status_code=200,
                url="https://www.instagram.com/reel/DgMZ0QMTquh/",
                text='<html>"comments_connection":{"edges":[]}</html>',
                cookies={"csrftoken": "ok_csrf"},
            )

        def close(self):
            pass

    with (
        patch("stages.s3_collectors.instagram_collector._CURL_AVAILABLE", False),
        patch("stages.s3_collectors.instagram_collector._create_http_session", return_value=_LoginSkipSess()),
    ):
        html, final_u, csrf = _fetch_post_html_sync("https://www.instagram.com/p/DgMZ0QMTquh/", "DgMZ0QMTquh")
    assert "comments_connection" in html
    assert "/reel/DgMZ0QMTquh/" in final_u
    assert csrf == "ok_csrf"
