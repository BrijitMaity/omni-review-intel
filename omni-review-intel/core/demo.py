"""
core/demo.py — Fast, deterministic demo state builder for omni-review-intel.
Generates a realistic multi-platform review analysis and styled Excel report for
the Sony WH-1000XM5 headphones without requiring external API keys.
"""

from __future__ import annotations

from pathlib import Path
from core.schemas import (
    TopicProfile,
    DiscoveredSource,
    AnalyzedComment,
    ExecutiveReview,
    AspectSummary,
    PipelineState,
)
from stages.s5_excel_builder import build_excel


def build_demo_state(output_dir: str = "outputs") -> PipelineState:
    """Build a complete sample PipelineState and generate its .xlsx report."""
    topic = TopicProfile(
        canonical_name="Sony WH-1000XM5 Wireless Noise-Canceling Headphones",
        brand="Sony",
        category="Consumer Electronics / Audio",
        input_type="product",
        short_description="Flagship wireless over-ear noise-canceling headphones from Sony.",
        search_queries=[
            "Sony WH-1000XM5 review",
            "Sony XM5 reddit worth it",
            "Sony WH-1000XM5 sound quality complaints",
            "Sony XM5 battery life real world",
            "Sony WH-1000XM5 vs XM4 comparison",
        ],
        visual_keywords=["Sony headphones", "over-ear", "ANC", "silver/black"],
    )

    sources = [
        DiscoveredSource(
            url="https://amazon.com/dp/B09XS7JWHH",
            platform="Amazon",
            title="Sony WH-1000XM5 Wireless Industry Leading Headphones - Amazon",
            scrape_status="success",
            comments_collected=120,
            positive_pct=78.0,
            neutral_pct=10.0,
            negative_pct=12.0,
        ),
        DiscoveredSource(
            url="https://youtube.com/watch?v=sample_review",
            platform="YouTube",
            title="Sony WH-1000XM5 In-Depth Audiophile Review - YouTube",
            scrape_status="success",
            comments_collected=85,
            positive_pct=82.0,
            neutral_pct=10.0,
            negative_pct=8.0,
        ),
        DiscoveredSource(
            url="https://reddit.com/r/headphones/comments/sample_xm5",
            platform="Reddit",
            title="6 Months With the Sony XM5s - Honest Community Thoughts",
            scrape_status="success",
            comments_collected=64,
            positive_pct=72.0,
            neutral_pct=10.0,
            negative_pct=18.0,
        ),
    ]

    raw_texts = [
        ("The active noise cancellation is the best in the market. Blocks airplane hum effortlessly.", "Positive", "ANC"),
        ("Soundstage is wide and punchy. Bass is deep without muddying vocals.", "Positive", "Sound Quality"),
        ("Extremely comfortable headband and earpads. I wear these for 8 hours daily without fatigue.", "Positive", "Comfort"),
        ("Auto-NC optimizer can be annoying when shifting head positions.", "Neutral", "ANC"),
        ("Does not fold into a compact size like the XM4 did. The case is bulkier.", "Negative", "Design"),
        ("Microphone quality in windy outdoor environments still lets background noise in.", "Negative", "Mic Quality"),
        ("Multipoint bluetooth works flawlessly between laptop and phone.", "Positive", "Connectivity"),
        ("Battery life easily hits 30 hours with ANC active. Quick charge is amazing.", "Positive", "Battery"),
    ] * 6

    analyzed_comments = [
        AnalyzedComment(
            comment_id=f"c_{i}",
            source_url="https://amazon.com/dp/B09XS7JWHH",
            platform="Amazon" if i % 3 == 0 else ("YouTube" if i % 3 == 1 else "Reddit"),
            original_text=text,
            english_text=text,
            sentiment=sent,
            confidence_score=0.92,
            aspect_tag=asp,
            is_spam=False,
        )
        for i, (text, sent, asp) in enumerate(raw_texts)
    ]

    aspect_summaries = [
        AspectSummary(
            aspect_name="ANC",
            mention_count=18,
            positive_pct=83.3,
            neutral_pct=11.1,
            negative_pct=5.6,
            key_takeaway="Industry-leading active noise cancellation, slight optimizer quirks.",
        ),
        AspectSummary(
            aspect_name="Sound Quality",
            mention_count=16,
            positive_pct=87.5,
            neutral_pct=6.25,
            negative_pct=6.25,
            key_takeaway="Clear highs, detailed mids, and powerful sub-bass response.",
        ),
        AspectSummary(
            aspect_name="Comfort",
            mention_count=14,
            positive_pct=85.7,
            neutral_pct=7.1,
            negative_pct=7.1,
            key_takeaway="Lightweight soft-fit leather, gentle clamp force for all-day wear.",
        ),
        AspectSummary(
            aspect_name="Battery",
            mention_count=12,
            positive_pct=91.7,
            neutral_pct=8.3,
            negative_pct=0.0,
            key_takeaway="Reliable 30+ hour battery life with rapid USB-C charging.",
        ),
        AspectSummary(
            aspect_name="Design",
            mention_count=10,
            positive_pct=40.0,
            neutral_pct=20.0,
            negative_pct=40.0,
            key_takeaway="Non-folding hinges result in a noticeably larger carrying case.",
        ),
    ]

    executive = ExecutiveReview(
        topic_name="Sony WH-1000XM5 Wireless Noise-Canceling Headphones",
        original_url="https://amazon.com/dp/B09XS7JWHH",
        overall_sentiment="Positive",
        ai_score_out_of_10=8.4,
        executive_summary=(
            "The Sony WH-1000XM5 represents a benchmark in consumer wireless active noise cancellation. "
            "Across Reddit, YouTube, and Amazon communities, users consistently praise its class-leading ANC "
            "attenuation and refined acoustic profile. While the redesigned non-folding chassis draws criticism "
            "from frequent travelers compared to its XM4 predecessor, daily comfort, battery endurance, and "
            "multipoint Bluetooth connectivity receive outstanding satisfaction marks."
        ),
        top_pros=[
            "Best-in-class active noise cancellation and transparency mode",
            "Balanced, dynamic sound signature with LDAC high-res support",
            "Superb 30-hour battery life with 3-minute quick charging",
            "Exceptionally comfortable for prolonged listening sessions",
        ],
        top_cons=[
            "Earcups do not fold inwards, requiring a larger travel case",
            "Hinges and matte finish can show finger oils over time",
            "Auto-NC optimizer cannot be completely locked to manual maximum",
        ],
        common_complaints=[
            "Bulky carrying case compared to previous XM4 generation",
            "Wind noise sometimes penetrates during outdoor phone calls",
        ],
        platform_comparison_notes=(
            "Amazon reviewers emphasize comfort and ANC for office work; "
            "Reddit audiophiles debate EQ settings and criticize the lack of folding; "
            "YouTube reviewers highlight microphone beamforming improvements."
        ),
        buying_or_action_verdict=(
            "Top recommendation for commuters, office workers, and remote professionals seeking "
            "unmatched silence and all-day audio comfort. If ultra-compact travel packing is critical, "
            "consider the older folding XM4."
        ),
        aspect_summaries=aspect_summaries,
    )

    xlsx_path = build_excel(
        review=executive,
        sources=sources,
        analyzed_comments=analyzed_comments,
        output_dir=output_dir,
    )

    return PipelineState(
        input_url="https://amazon.com/dp/B09XS7JWHH",
        topic=topic,
        sources=sources,
        raw_comments=[],
        analyzed_comments=analyzed_comments,
        executive_review=executive,
        output_xlsx_path=xlsx_path,
        total_comments_raw=269,
        total_comments_valid=len(analyzed_comments),
        positive_pct=77.1,
        neutral_pct=10.4,
        negative_pct=12.5,
    )
