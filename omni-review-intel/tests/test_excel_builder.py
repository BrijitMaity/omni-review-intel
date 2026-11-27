"""
tests/test_excel_builder.py — Unit tests for the Stage 5 Excel builder.

Verifies that the generated workbook has all 4 required sheets,
the correct column headers, frozen panes, and embedded charts.
"""

import pytest
from pathlib import Path
import tempfile

from openpyxl import load_workbook
from openpyxl.chart import PieChart, BarChart

from core.schemas import (
    AnalyzedComment,
    AspectSummary,
    DiscoveredSource,
    ExecutiveReview,
)
from stages.s5_excel_builder import build_excel


@pytest.fixture
def sample_review():
    return ExecutiveReview(
        topic_name="Test Headphones",
        original_url="https://example.com",
        overall_sentiment="Positive",
        ai_score_out_of_10=7.8,
        executive_summary="Great sound quality and build. Battery could be better.",
        top_pros=["Excellent audio", "Comfortable fit", "Good ANC"],
        top_cons=["Short battery life", "Expensive"],
        common_complaints=["Battery drains fast"],
        platform_comparison_notes="Reddit praises audio; Amazon users note connectivity issues.",
        buying_or_action_verdict="Recommended for audiophiles with budget.",
        aspect_summaries=[
            AspectSummary(
                aspect_name="Battery",
                mention_count=20,
                positive_pct=30.0,
                neutral_pct=20.0,
                negative_pct=50.0,
                key_takeaway="Battery life disappoints most users",
            )
        ],
    )


@pytest.fixture
def sample_sources():
    return [
        DiscoveredSource(
            url="https://youtube.com/watch?v=test",
            platform="YouTube",
            title="Best Headphones 2024",
            scrape_status="success",
            comments_collected=50,
            positive_pct=70.0,
            negative_pct=15.0,
        ),
        DiscoveredSource(
            url="https://reddit.com/r/headphones/test",
            platform="Reddit",
            title="Honest Reddit review",
            scrape_status="partial",
            comments_collected=12,
        ),
        DiscoveredSource(
            url="https://instagram.com/reel/C0000000000/",
            platform="Instagram",
            title="Honest Instagram review",
            scrape_status="partial",
            comments_collected=12,
        ),
        DiscoveredSource(
            url="https://flipkart.com/product-reviews/B09XYZ",
            platform="Flipkart",
            title="Honest Flipkart review",
            scrape_status="partial",
            comments_collected=12,
        ), 
        DiscoveredSource(
            url="https://amazon.in/product-reviews/B09XYZ",
            platform="Amazon",
            title="Honest Amazon review",
            scrape_status="partial",
            comments_collected=12,
        ),  
    ]


@pytest.fixture
def sample_comments():
    base = dict(
        source_url="https://youtube.com/watch?v=test",
        platform="YouTube",
        original_text="Love these headphones!",
        english_text="Love these headphones!",
        confidence_score=0.9,
        aspect_tag="Quality",
    )
    return [
        AnalyzedComment(**base, comment_id=f"c{i}", sentiment=s)
        for i, s in enumerate(["Positive", "Negative", "Neutral"])
    ]
    base = dict(
        source_url="https://instagram.com/reel/C0000000000/",
        platform="Instagram",
        original_text="Love these headphones!",
        english_text="Love these headphones!",
        confidence_score=0.9,
        aspect_tag="Quality",
    )
    return [
        AnalyzedComment(**base, comment_id=f"c{i}", sentiment=s)
        for i, s in enumerate(["Positive", "Negative", "Neutral"])
    ]
    base = dict(
        source_url="https://flipkart.com/product-reviews/B09XYZ",
        platform="Flipkart",
        original_text="Love these headphones!",
        english_text="Love these headphones!",
        confidence_score=0.9,
        aspect_tag="Quality",
    )
    return [
        AnalyzedComment(**base, comment_id=f"c{i}", sentiment=s)
        for i, s in enumerate(["Positive", "Negative", "Neutral"])
    ]
    base = dict(
        source_url="https://amazon.in/product-reviews/B09XYZ",
        platform="Amazon",
        original_text="Love these headphones!",
        english_text="Love these headphones!",
        confidence_score=0.9,
        aspect_tag="Quality",
    )
    return [
        AnalyzedComment(**base, comment_id=f"c{i}", sentiment=s)
        for i, s in enumerate(["Positive", "Negative", "Neutral"])
    ]


class TestExcelBuilder:
    def test_generates_file(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        assert Path(xlsx_path).exists()
        assert xlsx_path.endswith(".xlsx")

    def test_has_four_sheets(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
    def test_excutive_review_sheet(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
    def test_platform_summary_sheet(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
    def test_discovered_sources_sheet(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
    def test_all_reviews_sheet(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        sheet_titles = wb.sheetnames
        assert "Executive Review" in sheet_titles
        assert "Platform & Aspect Summary" in sheet_titles
        assert "Discovered Sources" in sheet_titles
        assert "All Reviews & Comments" in sheet_titles
        assert "👍 Good Reviews" in sheet_titles
        assert "👎 Bad Reviews" in sheet_titles
        assert "⚖️ Neutral Reviews" in sheet_titles
        assert wb.active.title == "All Reviews & Comments"
        

    def test_executive_review_has_topic_name(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        ws = wb["Executive Review"]
        # Title cell should contain the topic name
        assert "Test Headphones" in str(ws["A1"].value)

    def test_sources_sheet_has_hyperlinks(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        ws = wb["Discovered Sources"]
        # Row 2, column 4 should be a hyperlinked URL
        url_cell = ws.cell(row=2, column=4)
        assert url_cell.hyperlink is not None

    def test_comments_sheet_frozen_pane(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        ws = wb["All Reviews & Comments"]
        assert ws.freeze_panes == "A2"

    def test_comments_sheet_has_autofilter(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        ws = wb["All Reviews & Comments"]
        assert ws.auto_filter.ref is not None

    def test_executive_sheet_has_pie_chart(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        ws = wb["Executive Review"]
        charts = ws._charts
        assert any(isinstance(c, PieChart) for c in charts), "No PieChart in Executive Review"

    def test_platform_sheet_has_bar_chart(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        ws = wb["Platform & Aspect Summary"]
        charts = ws._charts
        assert any(isinstance(c, BarChart) for c in charts), "No BarChart in Platform sheet"

    def test_active_sheet_is_all_reviews(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        assert wb.active.title == "All Reviews & Comments"

    def test_exact_sentiment_counts_in_executive_review(self, tmp_path, sample_review, sample_sources, sample_comments):
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        ws = wb["Executive Review"]
        # Row 12: Positive, Row 13: Neutral, Row 14: Negative
        assert ws.cell(row=12, column=1).value == "Positive"
        assert ws.cell(row=12, column=2).value == 1  # 1 positive in sample_comments
        assert ws.cell(row=13, column=1).value == "Neutral"
        assert ws.cell(row=13, column=2).value == 1  # 1 neutral in sample_comments
        assert ws.cell(row=14, column=1).value == "Negative"
        assert ws.cell(row=14, column=2).value == 1  # 1 negative in sample_comments

    def test_target_reviews_sheet_separation(self, tmp_path, sample_review, sample_sources):
        comments = [
            AnalyzedComment(
                comment_id="t1",
                source_url="https://target.url",
                platform="YouTube",
                original_text="Target review 1",
                english_text="Target review 1",
                aspect_tag="Quality",
                sentiment="Positive",
                confidence_score=0.9,
                is_target=True,
            ),
            AnalyzedComment(
                comment_id="t2",
                source_url="https://target.url",
                platform="YouTube",
                original_text="Target review 2",
                english_text="Target review 2",
                aspect_tag="Design",
                sentiment="Negative",
                confidence_score=0.85,
                is_target=True,
            ),
            AnalyzedComment(
                comment_id="d1",
                source_url="https://other.url",
                platform="Reddit",
                original_text="Discovered review 1",
                english_text="Discovered review 1",
                aspect_tag="General",
                sentiment="Neutral",
                confidence_score=0.75,
                is_target=False,
            ),
        ]
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        assert "🎯 Target Reviews" in wb.sheetnames
        assert "🌐 Discovered Web Reviews" in wb.sheetnames

        ws_target = wb["🎯 Target Reviews"]
        # Header is row 1. Row 2 and row 3 should be target comments
        assert ws_target.cell(row=2, column=3).value == "Target review 1"
        assert ws_target.cell(row=3, column=3).value == "Target review 2"
        # Row 4 should not have any discovered review
        assert ws_target.cell(row=4, column=3).value is None

        ws_discovered = wb["🌐 Discovered Web Reviews"]
        assert ws_discovered.cell(row=2, column=3).value == "Discovered review 1"
        assert ws_discovered.cell(row=3, column=3).value is None

    def test_excel_handles_control_chars_and_leading_equals(self, tmp_path, sample_sources):
        comments = [
            AnalyzedComment(
                comment_id="eq1",
                source_url="https://youtube.com/watch?v=test",
                platform="YouTube",
                author="=AdminUser",
                original_text="===== BEST EPISODE EVER \x03\x07 =====",
                english_text="===== BEST EPISODE EVER =====",
                aspect_tag="General",
                sentiment="Positive",
                confidence_score=0.9,
                is_target=True,
            )
        ]
        review = ExecutiveReview(
            topic_name="=Weird Title \x04",
            original_url="https://youtube.com/watch?v=test",
            overall_sentiment="Positive",
            ai_score_out_of_10=8.0,
            executive_summary="=Summary \x00",
            top_pros=["=Great pro \x08"],
            top_cons=["=Minor con"],
            buying_or_action_verdict="Worth watching",
        )
        xlsx_path = build_excel(
            review=review,
            sources=sample_sources,
            analyzed_comments=comments,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path, data_only=False)
        ws = wb["🎯 Target Reviews"]
        cell = ws.cell(row=2, column=3)
        assert cell.data_type == "s"
        assert "BEST EPISODE EVER" in cell.value

    def test_empty_target_comments_do_not_get_disguised_with_discovered_web_rows(self, tmp_path, sample_review, sample_sources):
        web_only = [
            AnalyzedComment(
                comment_id="w1",
                source_url="https://blog.example.com/review",
                platform="Other",
                original_text="Discovered article review text",
                english_text="Discovered article review text",
                aspect_tag="General",
                sentiment="Neutral",
                confidence_score=0.8,
                is_target=False,
            )
        ]
        xlsx_path = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=web_only,
            output_dir=str(tmp_path),
        )
        wb = load_workbook(xlsx_path)
        assert "🌐 Discovered Web Reviews" in wb.sheetnames
        ws_target = wb["🎯 Target Reviews"]
        assert "No reviews directly from the target link" in str(ws_target.cell(row=2, column=3).value)
        ws_web = wb["🌐 Discovered Web Reviews"]
        assert ws_web.cell(row=2, column=3).value == "Discovered article review text"

    def test_build_excel_handles_same_second_collision(self, tmp_path, sample_review, sample_sources, sample_comments):
        p1 = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        p2 = build_excel(
            review=sample_review,
            sources=sample_sources,
            analyzed_comments=sample_comments,
            output_dir=str(tmp_path),
        )
        assert Path(p1).exists()
        assert Path(p2).exists()
        assert p1 != p2
