"""
tests/test_pipeline.py — End-to-end integration test of the full pipeline orchestrator.
"""

import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
import tempfile

from core.pipeline import run_pipeline
from core.schemas import (
    TopicProfile,
    DiscoveredSource,
    RawComment,
    AnalyzedComment,
    ExecutiveReview,
    AspectSummary,
)


@pytest.fixture
def mock_topic():
    return TopicProfile(
        canonical_name="Sony WH-1000XM5",
        brand="Sony",
        category="Headphones",
        input_type="product",
        search_queries=["sony xm5 review", "sony xm5 reddit", "sony xm5 complaints"],
    )


@pytest.fixture
def mock_sources():
    return [
        DiscoveredSource(
            url="https://youtube.com/watch?v=sample",
            platform="YouTube",
            title="Sony XM5 Review",
            scrape_status="success",
        ),
        DiscoveredSource(
            url="https://reddit.com/r/headphones/sample",
            platform="Reddit",
            title="XM5 Discussion",
            scrape_status="success",
        ),
        DiscoveredSource(
            url="https://instagram.com/reel/C0000000000/",
            platform="Instagram",
            title="XM5 Discussion",
            scrape_status="success",
        ),
        DiscoveredSource(
            url="https://flipkart.com/product-reviews/B09XYZ",
            platform="Flipkart",
            title="XM5 Discussion",
            scrape_status="success",
        ),
        DiscoveredSource(
            url="https://amazon.in/product-reviews/B09XYZ",
            platform="Amazon",
            title="XM5 Discussion",
            scrape_status="success",
        ),
    ]


@pytest.fixture
def mock_raw_comments():
    return [
        RawComment(
            source_url="https://youtube.com/watch?v=sample",
            platform="YouTube",
            original_text="Excellent sound quality and ANC!",
        ),
        RawComment(
            source_url="https://reddit.com/r/headphones/sample",
            platform="Reddit",
            original_text="Battery could be longer, but build is great.",
        ),
        RawComment(
            source_url="https://instagram.com/r/headphones/sample",
            platform="instagram",
            original_text="Build quality is good.",
        ),
        RawComment(
            source_url="https://twitter.com/r/headphones/sample",
            platform="twitter",
            original_text="Sound quality is great.",
        ),
        RawComment(
            source_url="https://flipkart.com/product-reviews/B09XYZ",
            platform="flipkart",
            original_text="Sound quality is great.",
        ),
        RawComment(
            source_url="https://amazon.in/product-reviews/B09XYZ",
            platform="amazon",
            original_text="Sound quality is great.",
        ),
    ]


@pytest.fixture
def mock_analyzed_comments():
    return [
        AnalyzedComment(
            comment_id="c1",
            source_url="https://youtube.com/watch?v=sample",
            platform="YouTube",
            original_text="Excellent sound quality and ANC!",
            english_text="Excellent sound quality and ANC!",
            sentiment="Positive",
            confidence_score=0.95,
            aspect_tag="Sound",
            is_spam=False,
        ),
        AnalyzedComment(
            comment_id="c2",
            source_url="https://reddit.com/r/headphones/sample",
            platform="Reddit",
            original_text="Battery could be longer, but build is great.",
            english_text="Battery could be longer, but build is great.",
            sentiment="Negative",
            confidence_score=0.88,
            aspect_tag="Battery",
            is_spam=False,
        ),
        AnalyzedComment(
            comment_id="c3",
            source_url="https://instagram.com/r/headphones/sample",
            platform="instagram",
            original_text="Build quality is good.",
            english_text="Build quality is good.",
            sentiment="Positive",
            confidence_score=0.90,
            aspect_tag="Build",
            is_spam=False,
        ),
        AnalyzedComment(
            comment_id="c4",
            source_url="https://twitter.com/r/headphones/sample",
            platform="twitter",
            original_text="Sound quality is great.",
            english_text="Sound quality is great.",
            sentiment="Positive",
            confidence_score=0.92,
            aspect_tag="Sound",
            is_spam=False,
        ),
    ]


@pytest.fixture
def mock_executive():
    return ExecutiveReview(
        topic_name="Sony WH-1000XM5",
        original_url="https://amazon.com/dp/B09XYZ",
        overall_sentiment="Positive",
        ai_score_out_of_10=8.2,
        executive_summary="Solid headphones with great ANC and sound.",
        top_pros=["Top ANC", "Comfortable"],
        top_cons=["Expensive"],
        buying_or_action_verdict="Highly recommended.",
        aspect_summaries=[
            AspectSummary(
                aspect_name="Sound",
                mention_count=1,
                positive_pct=100.0,
                key_takeaway="Superb clarity",
            )
        ],
        aspect_details={
            "Sound": {
                "positive_count": 1,
                "negative_count": 0,
                "neutral_count": 0,
                "positive_pct": 100.0,
                "negative_pct": 0.0,
                "neutral_pct": 0.0,
                "sentiment": "Positive",
                "key_takeaways": ["Superb clarity"],
            },
            "Battery": {
                "positive_count": 0,
                "negative_count": 1,
                "neutral_count": 0,
                "positive_pct": 0.0,
                "negative_pct": 100.0,
                "neutral_pct": 0.0,
                "sentiment": "Negative",
                "key_takeaways": ["Battery life is not good"],
            },
            "Build": {
                "positive_count": 1,
                "negative_count": 0,
                "neutral_count": 0,
                "positive_pct": 100.0,
                "negative_pct": 0.0,
                "neutral_pct": 0.0,
                "sentiment": "Positive",
                "key_takeaways": ["Build quality is good"],
            },
        },
        sentiment_breakdown={
            "positive_pct": 66.67,
            "negative_pct": 33.33,
            "neutral_pct": 0.0,
        },
        aspect_ranking=["Sound", "Battery", "Build"],
        sentiment_dist_by_platform={
            "YouTube": {
                "positive_pct": 100.0,
                "negative_pct": 0.0,
                "neutral_pct": 0.0,
            },
            "Reddit": {
                "positive_pct": 0.0,
                "negative_pct": 100.0,
                "neutral_pct": 0.0,
            },
        },
        platform_stats=[
            {
                "platform": "YouTube",
                "url": "https://youtube.com/watch?v=sample",
                "positive_pct": 100.0,
                "negative_pct": 0.0,
                "neutral_pct": 0.0,
                "comment_count": 1,
                "aspects": {
                    "Sound": {
                        "positive_count": 1,
                        "negative_count": 0,
                        "neutral_count": 0,
                        "positive_pct": 100.0,
                        "negative_pct": 0.0,
                        "neutral_pct": 0.0,
                        "sentiment": "Positive",
                        "key_takeaways": ["Superb clarity"],
                    }
                },
            },
            {
                "platform": "Reddit",
                "url": "https://reddit.com/r/headphones/sample",
                "positive_pct": 0.0,
                "negative_pct": 100.0,
                "neutral_pct": 0.0,
                "comment_count": 1,
                "aspects": {
                    "Battery": {
                        "positive_count": 0,
                        "negative_count": 1,
                        "neutral_count": 0,
                        "positive_pct": 0.0,
                        "negative_pct": 100.0,
                        "neutral_pct": 0.0,
                        "sentiment": "Negative",
                        "key_takeaways": ["Battery life is not good"],
                    }
                },
            },
            {
                "platform": "Instagram",
                "url": "https://instagram.com/r/headphones/sample",
                "positive_pct": 100.0,
                "negative_pct": 0.0,
                "neutral_pct": 0.0,
                "comment_count": 1,
                "aspects": {
                    "Build": {
                        "positive_count": 1,
                        "negative_count": 0,
                        "neutral_count": 0,
                        "positive_pct": 100.0,
                        "negative_pct": 0.0,
                        "neutral_pct": 0.0,
                        "sentiment": "Positive",
                        "key_takeaways": ["Build quality is good"],
                    }
                },
            },
            {
                "platform": "Twitter",
                "url": "https://twitter.com/r/headphones/sample",
                "positive_pct": 100.0,
                "negative_pct": 0.0,
                "neutral_pct": 0.0,
                "comment_count": 1,
                "aspects": {
                    "Sound": {
                        "positive_count": 1,
                        "negative_count": 0,
                        "neutral_count": 0,
                        "positive_pct": 100.0,
                        "negative_pct": 0.0,
                        "neutral_pct": 0.0,
                        "sentiment": "Positive",
                        "key_takeaways": ["Sound quality is great"],
                    }
                },
            },
        ],
        aspect_dist_by_platform={
            "YouTube": {
                "Sound": {
                    "positive_count": 1,
                    "negative_count": 0,
                    "neutral_count": 0,
                    "positive_pct": 100.0,
                    "negative_pct": 0.0,
                    "neutral_pct": 0.0,
                    "sentiment": "Positive",
                    "key_takeaways": ["Superb clarity"],
                }
            },
            "Reddit": {
                "Battery": {
                    "positive_count": 0,
                    "negative_count": 1,
                    "neutral_count": 0,
                    "positive_pct": 0.0,
                    "negative_pct": 100.0,
                    "neutral_pct": 0.0,
                    "sentiment": "Negative",
                    "key_takeaways": ["Battery life is not good"],
                }
            },
        },
        sentiment_dist_by_aspect={
            "Sound": {
                "positive_pct": 100.0,
                "negative_pct": 0.0,
                "neutral_pct": 0.0,
            },
            "Battery": {
                "positive_pct": 0.0,
                "negative_pct": 100.0,
                "neutral_pct": 0.0,
            },
        },
        aspect_ranking_by_platform=["YouTube", "Reddit","instagram", "twitter"],
    )


class TestPipelineOrchestrator:
    @pytest.mark.asyncio
    async def test_full_pipeline_run_success(
        self,
        tmp_path,
        mock_topic,
        mock_sources,
        mock_raw_comments,
        mock_analyzed_comments,
        mock_executive,
    ):
        progress_events = []

        async def _cb(stage: str, msg: str):
            progress_events.append((stage, msg))

        with patch("core.pipeline.analyze_input", new=AsyncMock(return_value=(mock_topic, []))), \
             patch("core.pipeline.discover_sources", new=AsyncMock(return_value=mock_sources)), \
             patch("core.pipeline.collect_all", new=AsyncMock(return_value=(mock_raw_comments, mock_sources))), \
             patch("core.pipeline.map_analyze", new=AsyncMock(return_value=mock_analyzed_comments)), \
             patch("core.pipeline.reduce_synthesize", new=AsyncMock(return_value=(mock_executive, mock_analyzed_comments))):

            state = await run_pipeline(
                input_url="https://amazon.com/dp/B09XYZ",
                output_dir=str(tmp_path),
                progress_cb=_cb,
            )

            assert state.topic.canonical_name == "Sony WH-1000XM5"
            assert len(state.sources) == len(mock_sources)
            assert state.total_comments_raw == len(mock_raw_comments)
            assert state.total_comments_valid == len(mock_analyzed_comments)
            assert state.positive_pct == 75.0
            assert state.negative_pct == 25.0
            assert state.executive_review.ai_score_out_of_10 == 8.2
            assert Path(state.output_xlsx_path).exists()
            assert len(progress_events) >= 5

    @pytest.mark.asyncio
    async def test_pipeline_does_not_fabricate_fake_comments_on_empty_collection(
        self,
        tmp_path,
        mock_topic,
        mock_executive,
    ):
        empty_sources = [
            DiscoveredSource(
                url="https://www.youtube.com/watch?v=Z4aMdO8K0XE",
                platform="YouTube",
                title="[Original Input]",
                snippet="",
                scrape_status="partial",
                error_message="YouTube /watch HTTP 429 rate-limited",
                is_target=True,
            )
        ]

        with patch("core.pipeline.analyze_input", new=AsyncMock(return_value=(mock_topic, []))), \
             patch("core.pipeline.discover_sources", new=AsyncMock(return_value=empty_sources)), \
             patch("core.pipeline.collect_all", new=AsyncMock(return_value=([], empty_sources))), \
             patch("core.pipeline.map_analyze", new=AsyncMock(return_value=[])), \
             patch("core.pipeline.reduce_synthesize", new=AsyncMock(return_value=(mock_executive, []))):

            state = await run_pipeline(
                input_url="https://www.youtube.com/watch?v=Z4aMdO8K0XE",
                output_dir=str(tmp_path),
            )

            assert state.total_comments_raw == 0
            assert state.raw_comments == []
            assert any("0 real comments" in err for err in state.stage_errors)