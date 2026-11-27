"""
tests/test_s4_ai_analyzer.py — Unit tests for Stage 4 Map-Reduce AI analysis.
"""

import pytest
from core.schemas import AnalyzedComment, RawComment, TopicProfile, AspectSummary
from stages.s4_ai_analyzer import (
    _pre_clean,
    _chunk,
    _compute_stats,
    _sample_comments,
    _stub_review,
)


class TestMapPhaseHelpers:
    def test_pre_clean_deduplication(self):
        c1 = RawComment(source_url="https://a.com", original_text="Great sound quality!")
        c2 = RawComment(source_url="https://b.com", original_text="  Great  sound   quality!  ")
        c3 = RawComment(source_url="https://c.com", original_text="Different comment")

        cleaned = _pre_clean([c1, c2, c3])
        assert len(cleaned) == 2
        assert cleaned[0].original_text == "Great sound quality!"
        assert cleaned[1].original_text == "Different comment"
        # RawComment strips leading/trailing on creation, but preserves internal spaces
        assert c2.original_text == "Great  sound   quality!"

    def test_pre_clean_empty_removal(self):
        c1 = RawComment(source_url="https://a.com", original_text="Valid comment")
        cleaned = _pre_clean([c1])
        assert len(cleaned) == 1

    def test_chunk(self):
        items = list(range(10))
        chunks = list(_chunk(items, 3))
        assert len(chunks) == 4
        assert chunks[0] == [0, 1, 2]
        assert chunks[1] == [3, 4, 5]
        assert chunks[2] == [6, 7, 8]
        assert chunks[3] == [9]


class TestReducePhaseHelpers:
    @pytest.fixture
    def sample_analyzed(self):
        comments = []
        # 3 positive Quality
        for i in range(3):
            comments.append(
                AnalyzedComment(
                    comment_id=f"p{i}",
                    source_url="https://yt.com",
                    platform="YouTube",
                    original_text="Love it",
                    english_text="Love it",
                    sentiment="Positive",
                    confidence_score=0.9,
                    aspect_tag="Quality",
                )
            )
        # 2 negative Battery
        for i in range(2):
            comments.append(
                AnalyzedComment(
                    comment_id=f"n{i}",
                    source_url="https://reddit.com",
                    platform="Reddit",
                    original_text="Battery dies",
                    english_text="Battery dies",
                    sentiment="Negative",
                    confidence_score=0.85,
                    aspect_tag="Battery",
                )
            )
        # 1 neutral General
        comments.append(
            AnalyzedComment(
                comment_id="neu0",
                source_url="https://yt.com",
                platform="YouTube",
                original_text="It is okay",
                english_text="It is okay",
                sentiment="Neutral",
                confidence_score=0.6,
                aspect_tag="General",
            )
        )
        return comments

    def test_compute_stats_accuracy(self, sample_analyzed):
        stats = _compute_stats(sample_analyzed)
        assert stats["total"] == 6
        assert stats["positive"] == 3
        assert stats["negative"] == 2
        assert stats["neutral"] == 1
        assert stats["positive_pct"] == 50.0
        assert stats["negative_pct"] == 33.3
        assert stats["neutral_pct"] == 16.7

        # Check platform breakdown
        assert "YouTube" in stats["platform_stats"]
        assert "Reddit" in stats["platform_stats"]
        assert stats["platform_stats"]["YouTube"]["total"] == 4
        assert stats["platform_stats"]["Reddit"]["total"] == 2

        # Check aspect breakdown
        assert "Quality" in stats["aspect_stats"]
        assert "Battery" in stats["aspect_stats"]
        assert stats["aspect_stats"]["Quality"]["mention_count"] == 3
        assert stats["aspect_stats"]["Quality"]["positive_pct"] == 100.0
        assert stats["aspect_stats"]["Battery"]["mention_count"] == 2
        assert stats["aspect_stats"]["Battery"]["negative_pct"] == 100.0

    def test_compute_stats_empty(self):
        stats = _compute_stats([])
        assert stats["total"] == 0
        assert stats["positive_pct"] == 0.0

    def test_sample_comments(self, sample_analyzed):
        samples = _sample_comments(sample_analyzed, n=2)
        assert len(samples["Positive"]) == 2
        assert len(samples["Negative"]) == 2
        assert len(samples["Neutral"]) == 1

    def test_stub_review_creation(self):
        topic = TopicProfile(
            canonical_name="Test Product",
            input_type="product",
            search_queries=["test review", "test reddit", "test problems"],
        )
        review = _stub_review(topic, "https://example.com/test")
        assert review.topic_name == "Test Product"
        assert review.ai_score_out_of_10 == 5.0
        assert review.overall_sentiment == "Neutral"
        assert len(review.top_pros) > 0
        assert len(review.top_cons) > 0

    def test_heuristic_negation_and_emoji_accuracy(self):
        from stages.s4_ai_analyzer import _heuristic_analyze_comment

        neg = _heuristic_analyze_comment(
            RawComment(source_url="https://yt.com", original_text="Battery life is not good at all"),
            "Test Product",
        )
        assert neg.sentiment == "Negative"

        safe = _heuristic_analyze_comment(
            RawComment(source_url="https://yt.com", original_text="No issues at all, not bad!"),
            "Test Product",
        )
        assert safe.sentiment == "Positive"

        emoji_pos = _heuristic_analyze_comment(
            RawComment(source_url="https://yt.com", original_text="MC Stan entry 🔥❤️💯"),
            "Test Product",
        )
        assert emoji_pos.sentiment == "Positive"

    def test_aspect_detection_avoids_anc_substring_false_positives_and_non_product_verdict(self):
        from stages.s4_ai_analyzer import _heuristic_analyze_comment

        c = _heuristic_analyze_comment(
            RawComment(source_url="https://yt.com", original_text="Great performance and smooth software"),
            "Some Phone",
        )
        assert c.aspect_tag == "Performance"

        anc = _heuristic_analyze_comment(
            RawComment(source_url="https://yt.com", original_text="ANC blocks airplane noise easily"),
            "Test Headphones",
        )
        assert anc.aspect_tag == "ANC"

        vid_topic = TopicProfile(
            canonical_name="Music Video",
            input_type="video",
            search_queries=["music video review", "music video reddit", "music video comments"],
        )
        sample_c = [
            AnalyzedComment(
                comment_id="v1",
                source_url="https://yt.com",
                platform="YouTube",
                original_text="Awesome editing and song!",
                english_text="Awesome editing and song!",
                sentiment="Positive",
                confidence_score=0.9,
                aspect_tag="Content & Creative",
            )
        ]
        vid_review = _stub_review(
            vid_topic,
            "https://youtube.com/watch?v=test",
            stats={"total": 1, "positive_pct": 100.0, "neutral_pct": 0.0, "negative_pct": 0.0},
            comments=sample_c,
        )
        assert "purchase" not in vid_review.buying_or_action_verdict.lower()
        assert "buyers" not in vid_review.buying_or_action_verdict.lower()
