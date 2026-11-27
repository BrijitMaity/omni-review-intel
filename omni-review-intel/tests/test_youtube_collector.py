"""tests/test_youtube_collector.py — Unit tests for YouTube InnerTube multi-phase pagination."""
from core.schemas import DiscoveredSource, RawComment
from stages.s3_collectors.youtube_collector import (
    _extract_innertube_page,
    _extract_sort_tokens,
    _parse_yt_like_count,
)


def test_parse_yt_like_count():
    assert _parse_yt_like_count("42") == 42
    assert _parse_yt_like_count("1,200") == 1200
    assert _parse_yt_like_count("1.5K") == 1500
    assert _parse_yt_like_count("2M") == 2000000
    assert _parse_yt_like_count(None) == 0


def test_extract_sort_tokens():
    data = {
        "onResponseReceivedEndpoints": [
            {
                "reloadContinuationItemsCommand": {
                    "continuationItems": [
                        {
                            "commentsHeaderRenderer": {
                                "sortMenu": {
                                    "sortFilterSubMenuRenderer": {
                                        "subMenuItems": [
                                            {
                                                "title": "Top comments",
                                                "serviceEndpoint": {
                                                    "continuationCommand": {"token": "TOP_TOK%3D"}
                                                },
                                            },
                                            {
                                                "title": "Newest first",
                                                "serviceEndpoint": {
                                                    "continuationCommand": {"token": "NEW_TOK%3D"}
                                                },
                                            },
                                        ]
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        ]
    }
    top_tok, new_tok = _extract_sort_tokens(data)
    assert top_tok == "TOP_TOK="
    assert new_tok == "NEW_TOK="


def test_extract_innertube_page_dedup_and_replies():
    data = {
        "frameworkUpdates": {
            "entityBatchUpdate": {
                "mutations": [
                    {
                        "payload": {
                            "commentEntityPayload": {
                                "commentId": "c1",
                                "properties": {"content": {"content": "Great video!"}, "publishedTime": "1d"},
                                "author": {"displayName": "@user1"},
                                "toolbar": {"likeCountNotliked": "1.2K"},
                            }
                        }
                    }
                ]
            }
        },
        "onResponseReceivedEndpoints": [
            {
                "appendContinuationItemsAction": {
                    "continuationItems": [
                        {
                            "commentThreadRenderer": {
                                "comment": {
                                    "commentRenderer": {
                                        "commentId": "c1",
                                        "contentText": {"runs": [{"text": "Great video!"}]},
                                    }
                                },
                                "replies": {
                                    "commentRepliesRenderer": {
                                        "contents": [
                                            {
                                                "continuationItemRenderer": {
                                                    "button": {
                                                        "buttonRenderer": {
                                                            "command": {
                                                                "continuationCommand": {"token": "REPLY_TOK_1"}
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                },
                            }
                        },
                        {
                            "continuationItemRenderer": {
                                "continuationEndpoint": {"continuationCommand": {"token": "NEXT_TOK_2"}}
                            }
                        },
                    ]
                }
            }
        ],
    }

    seen_ids = set()
    visited = set()
    seen_replies = set()
    comments, raw_cnt, next_tok, reply_toks = _extract_innertube_page(
        data=data,
        video_id="abc12345678",
        source_url="https://www.youtube.com/watch?v=abc12345678",
        is_target=True,
        seen_ids=seen_ids,
        max_items=5000,
        current_count=0,
        visited_tokens=visited,
        seen_reply_tokens=seen_replies,
    )
    # Deduplicates modern + legacy c1 into 1 comment while tracking both in raw_cnt
    assert len(comments) == 1
    assert comments[0].comment_id == "c1"
    assert comments[0].author == "@user1"
    assert raw_cnt == 2
    assert next_tok == "NEXT_TOK_2"
    assert reply_toks == ["REPLY_TOK_1"]


def test_extract_sort_tokens_engagement_panel():
    data = {
        "engagementPanels": [
            {
                "engagementPanelSectionListRenderer": {
                    "panelIdentifier": "engagement-panel-comments-section",
                    "targetId": "comments-section",
                    "header": {
                        "engagementPanelTitleHeaderRenderer": {
                            "menu": {
                                "sortFilterSubMenuRenderer": {
                                    "subMenuItems": [
                                        {
                                            "title": "Top comments",
                                            "serviceEndpoint": {"continuationCommand": {"token": "PANEL_TOP%3D"}},
                                        },
                                        {
                                            "title": "Newest first",
                                            "serviceEndpoint": {"continuationCommand": {"token": "PANEL_NEW%3D"}},
                                        },
                                    ]
                                }
                            }
                        }
                    },
                }
            }
        ]
    }
    top_tok, new_tok = _extract_sort_tokens(data)
    assert top_tok == "PANEL_TOP="
    assert new_tok == "PANEL_NEW="


def test_innertube_bypasses_watch_429_via_embed_and_next():
    import asyncio
    from unittest.mock import MagicMock, patch
    from stages.s3_collectors.youtube_collector import _collect_via_innertube_curl

    class _Resp:
        def __init__(self, status_code, text="", json_data=None, url="https://www.youtube.com/watch?v=abc12345678"):
            self.status_code = status_code
            self.text = text
            self._json = json_data
            self.url = url

        def json(self):
            return self._json

    class _FakeSess:
        def __init__(self):
            self.cookies = MagicMock()

        def get(self, url, timeout=15):
            if "/watch" in url:
                return _Resp(429, "sorry", url="https://www.google.com/sorry/index")
            if "/embed/" in url:
                return _Resp(
                    200,
                    '<html><script>ytcfg.set({"INNERTUBE_API_KEY":"AIzaTestKey","clientVersion":"2.20261125.00.00"});</script></html>',
                    url=url,
                )
            return _Resp(404)

        def post(self, url, json=None, timeout=20):
            if json and "videoId" in json:
                return _Resp(
                    200,
                    json_data={
                        "engagementPanels": [
                            {
                                "engagementPanelSectionListRenderer": {
                                    "panelIdentifier": "engagement-panel-comments-section",
                                    "content": {
                                        "sectionListRenderer": {
                                            "contents": [
                                                {
                                                    "itemSectionRenderer": {
                                                        "sectionIdentifier": "comment-item-section",
                                                        "contents": [
                                                            {
                                                                "continuationItemRenderer": {
                                                                    "continuationEndpoint": {
                                                                        "continuationCommand": {"token": "INIT_TOK"}
                                                                    }
                                                                }
                                                            }
                                                        ],
                                                    }
                                                }
                                            ]
                                        }
                                    },
                                }
                            }
                        ]
                    },
                )
            if json and json.get("continuation") == "INIT_TOK":
                return _Resp(
                    200,
                    json_data={
                        "frameworkUpdates": {
                            "entityBatchUpdate": {
                                "mutations": [
                                    {
                                        "payload": {
                                            "commentEntityPayload": {
                                                "commentId": "c_bypass_1",
                                                "properties": {
                                                    "content": {"content": "Hilarious episode!"},
                                                    "publishedTime": "2 hours ago",
                                                },
                                                "author": {"displayName": "@viewer1"},
                                                "toolbar": {"likeCountNotliked": "42"},
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    },
                )
            return _Resp(404)

        def close(self):
            pass

    src = DiscoveredSource(url="https://youtu.be/abc12345678", platform="YouTube", is_target=True)
    with patch("curl_cffi.requests.Session", return_value=_FakeSess()), patch("httpx.Client", return_value=_FakeSess()):
        comments, updated = asyncio.run(_collect_via_innertube_curl("abc12345678", src))

    assert len(comments) == 1
    assert comments[0].comment_id == "c_bypass_1"
    assert comments[0].original_text == "Hilarious episode!"
    assert updated.collector_used == "youtube_innertube"
    assert updated.scrape_status == "success"


def test_downloader_preserves_partial_and_ytdlp_uses_extractor_args():
    import asyncio
    from unittest.mock import MagicMock, patch
    from stages.s3_collectors.youtube_collector import _collect_via_downloader, _collect_via_ytdlp

    def _failing_gen(url, sort_by=1):
        yield {"cid": "c1", "text": "First real comment", "author": "@a1", "time": "1d", "votes": "5"}
        raise RuntimeError("Simulated 429 mid-stream")

    fake_dl = MagicMock()
    fake_dl.session = MagicMock()
    fake_dl.get_comments_from_url.side_effect = _failing_gen

    src1 = DiscoveredSource(url="https://www.youtube.com/watch?v=abc12345678", platform="YouTube", is_target=True)
    with patch("youtube_comment_downloader.YoutubeCommentDownloader", return_value=fake_dl):
        comments, updated = asyncio.run(_collect_via_downloader("abc12345678", src1))

    assert len(comments) == 1
    assert comments[0].original_text == "First real comment"
    assert updated.collector_used == "youtube_downloader"

    captured_opts = {}

    class _FakeYDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False):
            return {"comments": [{"id": "d1", "text": "Great vid", "author": "u1", "timestamp": "123"}]}

    from config import settings

    src2 = DiscoveredSource(url="https://www.youtube.com/watch?v=abc12345678", platform="YouTube", is_target=True)
    orig_max = settings.max_comments_per_url
    try:
        settings.max_comments_per_url = 5000
        with patch("yt_dlp.YoutubeDL", _FakeYDL):
            comments2, _ = asyncio.run(_collect_via_ytdlp("abc12345678", src2))
    finally:
        settings.max_comments_per_url = orig_max

    assert len(comments2) == 1
    assert "max_comments" not in captured_opts
    assert captured_opts["extractor_args"]["youtube"]["max_comments"][0] == "5000"
    assert captured_opts["extractor_args"]["youtube"]["player_client"][0] == "visionos"


def test_innertube_retries_incomplete_http200_and_uncapped_cache_rejects_partial():
    import asyncio
    from unittest.mock import MagicMock, patch
    from config import settings
    from stages.s3_collectors.youtube_collector import _collect_via_innertube_curl, collect_youtube

    class _Resp:
        def __init__(self, status_code, text="", json_data=None, url="https://www.youtube.com/embed/abc12345678"):
            self.status_code = status_code
            self.text = text
            self._json = json_data
            self.url = url

        def json(self):
            return self._json

    post_calls = {"page1": 0}

    class _FakeSess:
        def __init__(self):
            self.cookies = MagicMock()

        def get(self, url, timeout=15):
            if "/watch" in url:
                return _Resp(429, "sorry", url="https://www.google.com/sorry/index")
            if "/embed/" in url:
                return _Resp(
                    200,
                    '<html><script>ytcfg.set({"INNERTUBE_API_KEY":"AIzaTestKey","clientVersion":"2.20261125.00.00","VISITOR_DATA":"VIS123"});</script></html>',
                    url=url,
                )
            return _Resp(404)

        def post(self, url, json=None, headers=None, timeout=20):
            if json and "videoId" in json:
                return _Resp(
                    200,
                    json_data={
                        "responseContext": {"visitorData": "VIS_NEXT"},
                        "engagementPanels": [
                            {
                                "engagementPanelSectionListRenderer": {
                                    "panelIdentifier": "engagement-panel-comments-section",
                                    "header": {
                                        "engagementPanelTitleHeaderRenderer": {
                                            "contextualInfo": {"simpleText": "56K"}
                                        }
                                    },
                                    "content": {
                                        "sectionListRenderer": {
                                            "contents": [
                                                {
                                                    "itemSectionRenderer": {
                                                        "sectionIdentifier": "comment-item-section",
                                                        "contents": [
                                                            {
                                                                "continuationItemRenderer": {
                                                                    "continuationEndpoint": {
                                                                        "continuationCommand": {"token": "P1"}
                                                                    }
                                                                }
                                                            }
                                                        ],
                                                    }
                                                }
                                            ]
                                        }
                                    },
                                }
                            }
                        ],
                    },
                )
            if json and json.get("continuation") == "P1":
                post_calls["page1"] += 1
                if post_calls["page1"] == 1:
                    # Simulate YouTube incomplete HTTP 200 (no onResponseReceivedEndpoints!)
                    return _Resp(200, json_data={"responseContext": {"visitorData": "VIS_2"}, "trackingParams": "xyz"})
                assert (headers or {}).get("X-Goog-Visitor-Id") in ("VIS_NEXT", "VIS_2", "VIS123")
                return _Resp(
                    200,
                    json_data={
                        "frameworkUpdates": {
                            "entityBatchUpdate": {
                                "mutations": [
                                    {
                                        "payload": {
                                            "commentEntityPayload": {
                                                "commentId": "recovered_c1",
                                                "properties": {"content": {"content": "Recovered after incomplete 200!"}, "publishedTime": "1d"},
                                                "author": {"displayName": "@resilient"},
                                                "toolbar": {"likeCountNotliked": "10"},
                                            }
                                        }
                                    }
                                ]
                            }
                        },
                        "onResponseReceivedEndpoints": [{"appendContinuationItemsAction": {"continuationItems": []}}],
                    },
                )
            return _Resp(404)

        def close(self):
            pass

    meta_out = {}
    src = DiscoveredSource(url="https://youtu.be/abc12345678", platform="YouTube", is_target=True)
    with patch("curl_cffi.requests.Session", return_value=_FakeSess()), patch("httpx.Client", return_value=_FakeSess()), patch("time.sleep"):
        comments, updated = asyncio.run(_collect_via_innertube_curl("abc12345678", src, meta_out=meta_out))

    assert post_calls["page1"] == 2
    assert meta_out["est_total"] == 56000
    assert len(comments) == 1
    assert comments[0].comment_id == "recovered_c1"

    # Uncapped mode MUST reject partial cached result and re-fetch/escalate
    orig_max = settings.max_comments_per_url
    try:
        settings.max_comments_per_url = 0
        mock_cache = MagicMock()
        mock_cache.get.return_value = {
            "comments": [comments[0].model_dump()],
            "expected_total": 56000,
            "scrape_status": "partial",
        }
        dl_c = RawComment(comment_id="dl_c2", source_url=src.url, platform="YouTube", original_text="Extra from downloader")
        with (
            patch("stages.s3_collectors.youtube_collector.get_cache", return_value=mock_cache),
            patch("stages.s3_collectors.youtube_collector._collect_via_innertube_curl", return_value=([comments[0]], src)),
            patch("stages.s3_collectors.youtube_collector._collect_via_downloader", return_value=([dl_c], src)),
            patch("stages.s3_collectors.youtube_collector._collect_via_ytdlp", return_value=([], src)),
        ):
            res_comments, res_src = asyncio.run(collect_youtube(src))
        assert len(res_comments) == 2
        assert {c.comment_id for c in res_comments} == {"recovered_c1", "dl_c2"}
    finally:
        settings.max_comments_per_url = orig_max
