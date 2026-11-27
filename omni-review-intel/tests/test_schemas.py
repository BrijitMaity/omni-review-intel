"""
tests/test_schemas.py — Unit tests for Pydantic schema serialization and validation.
"""

import pytest
from core.schemas import (
    TopicProfile,
    DiscoveredSource,
    RawComment,
    AnalyzedComment,
    BatchAnalysisResult,
    AspectSummary,
    ExecutiveReview,
    PipelineState,
)


class TestTopicProfile:
    def test_valid_creation(self):
        tp = TopicProfile(
            canonical_name="Sony WH-1000XM5",
            brand="Sony",
            category="Wireless Headphones",
            input_type="product",
            search_queries=["sony wh1000xm5 review", "sony xm5 reddit", "xm5 problems"],
        )
        assert tp.canonical_name == "Sony WH-1000XM5"
        assert tp.input_type == "product"

    def test_model_dump_and_validate(self):
        tp = TopicProfile(
            canonical_name="Test",
            input_type="video",
            search_queries=["query1", "query2", "query3"],
        )
        data = tp.model_dump()
        restored = TopicProfile.model_validate(data)
        assert restored.canonical_name == tp.canonical_name

    def test_invalid_input_type(self):
        with pytest.raises(Exception):
            TopicProfile(
                canonical_name="X",
                input_type="invalid_type",  # type: ignore
                search_queries=["q1", "q2", "q3"],
            )


class TestDiscoveredSource:
    def test_default_source_id_generated(self):
        src = DiscoveredSource(url="https://example.com")
        assert len(src.source_id) > 0
        assert src.scrape_status == "pending"

    def test_platform_detection_field(self):
        src = DiscoveredSource(url="https://reddit.com/r/test", platform="Reddit")
        assert src.platform == "Reddit"


class TestRawComment:
    def test_strips_whitespace(self):
        c = RawComment(source_url="https://x.com", original_text="  Hello  ")
        assert c.original_text == "Hello"

    def test_requires_original_text(self):
        with pytest.raises(Exception):
            RawComment(source_url="https://x.com", original_text="")

    def test_rating_bounds(self):
        with pytest.raises(Exception):
            RawComment(source_url="https://x.com", original_text="ok", rating=6.0)


class TestAnalyzedComment:
    def test_valid_creation(self):
        ac = AnalyzedComment(
            comment_id="abc123",
            source_url="https://youtube.com",
            original_text="Great product!",
            english_text="Great product!",
            sentiment="Positive",
            confidence_score=0.95,
            aspect_tag="Quality",
        )
        assert ac.sentiment == "Positive"
        assert ac.is_spam is False


class TestBatchAnalysisResult:
    def test_empty_batch(self):
        bar = BatchAnalysisResult()
        assert bar.comments == []

    def test_validates_nested_comments(self):
        bar = BatchAnalysisResult(
            comments=[
                AnalyzedComment(
                    comment_id="x1",
                    source_url="https://test.com",
                    original_text="Good",
                    english_text="Good",
                    sentiment="Positive",
                    confidence_score=0.8,
                    aspect_tag="General",
                )
            ]
        )
        assert len(bar.comments) == 1


class TestExecutiveReview:
    def test_valid_review(self):
        er = ExecutiveReview(
            topic_name="Test Product",
            original_url="https://test.com",
            overall_sentiment="Positive",
            ai_score_out_of_10=7.5,
            executive_summary="A solid product.",
            top_pros=["Good quality", "Fair price"],
            top_cons=["Short battery"],
            buying_or_action_verdict="Recommended for casual users.",
        )
        assert er.ai_score_out_of_10 == 7.5

    def test_score_out_of_range(self):
        with pytest.raises(Exception):
            ExecutiveReview(
                topic_name="X",
                original_url="https://x.com",
                overall_sentiment="Neutral",
                ai_score_out_of_10=11.0,  # > 10
                executive_summary="...",
                top_pros=["a"],
                top_cons=["b"],
                buying_or_action_verdict="...",
            )


class TestPipelineState:
    def test_default_values(self):
        ps = PipelineState(input_url="https://example.com/item")
        assert ps.input_url == "https://example.com/item"
        assert len(ps.run_id) > 0
        assert ps.total_comments_raw == 0
        assert ps.total_comments_valid == 0
        assert ps.positive_pct == 0.0
        assert ps.neutral_pct == 0.0
        assert ps.negative_pct == 0.0
        assert ps.sources == []
        assert ps.raw_comments == []
        assert ps.analyzed_comments == []
        assert ps.stage_errors == []

    def test_custom_stats_and_serialization(self):
        ps = PipelineState(
            input_url="https://example.com/item",
            total_comments_raw=100,
            total_comments_valid=85,
            positive_pct=60.0,
            neutral_pct=25.0,
            negative_pct=15.0,
        )
        assert ps.total_comments_raw == 100
        assert ps.total_comments_valid == 85
        assert ps.positive_pct == 60.0
        assert ps.neutral_pct == 25.0
        assert ps.negative_pct == 15.0

        dump = ps.model_dump()
        restored = PipelineState.model_validate(dump)
        assert restored.total_comments_valid == 85
        assert restored.positive_pct == 60.0
