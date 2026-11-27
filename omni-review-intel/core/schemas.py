"""
core/schemas.py — All Pydantic v2 data models for omni-review-intel.
These models are the inter-stage contracts; every piece of data flowing
through the pipeline is validated against one of these schemas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
import uuid


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Input & Entity Identification
# ─────────────────────────────────────────────────────────────────────────────

InputType = Literal["product", "video", "image", "article"]


class TopicProfile(BaseModel):
    """Canonical entity description produced by Stage 1 (entity_extraction LLM)."""

    canonical_name: str = Field(
        ..., description="Exact name of the product/topic, e.g. 'Sony WH-1000XM5 Headphones'"
    )
    brand: str = Field(default="", description="Brand name, empty if N/A")
    category: str = Field(
        default="", description="Category, e.g. 'Wireless Headphones', 'Smartphone'"
    )
    input_type: InputType = Field(..., description="Detected URL type")
    short_description: str = Field(
        default="", description="1-2 sentence description of the topic"
    )
    search_queries: list[str] = Field(
        ...,
        min_length=3,
        max_length=8,
        description="High-signal web queries to find reviews/discussions",
    )
    visual_keywords: list[str] = Field(
        default_factory=list,
        description="Visual search terms (used for reverse-image fallback)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Discovered Sources
# ─────────────────────────────────────────────────────────────────────────────

ScrapeStatus = Literal["pending", "success", "partial", "failed", "skipped"]
PlatformType = Literal[
    "YouTube", "Reddit", "Amazon", "Flipkart", "Facebook", "Twitter", "Instagram", "TikTok", "Blog", "Forum",
    "News", "ECommerce", "Other"
]

_PLATFORM_MAP: dict[str, PlatformType] = {
    "youtube": "YouTube",
    "reddit": "Reddit",
    "amazon": "Amazon",
    "flipkart": "Flipkart",
    "facebook": "Facebook",
    "fb": "Facebook",
    "twitter": "Twitter",
    "x": "Twitter",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "blog": "Blog",
    "forum": "Forum",
    "news": "News",
    "ecommerce": "ECommerce",
    "e-commerce": "ECommerce",
    "other": "Other",
}


def normalize_platform(v: str) -> str:
    """Normalize platform string case-insensitively to valid PlatformType."""
    if isinstance(v, str):
        cleaned = v.strip().lower()
        if cleaned in _PLATFORM_MAP:
            return _PLATFORM_MAP[cleaned]
    return v


class DiscoveredSource(BaseModel):
    """One discovered URL that will (or was) scraped for comments."""

    source_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    url: str = Field(..., description="Canonical URL after tracking-param stripping")
    domain: str = Field(default="", description="Root domain, e.g. reddit.com")
    platform: PlatformType = Field(default="Other")
    title: str = Field(default="", description="Page title from search result snippet")
    snippet: str = Field(default="", description="Short excerpt from search result")
    scrape_status: ScrapeStatus = Field(default="pending")
    collector_used: str = Field(
        default="", description="Collector module that handled this URL"
    )
    is_target: bool = Field(
        default=False, description="True if this is the user's targeted input URL"
    )
    comments_collected: int = Field(default=0)
    positive_pct: float = Field(default=0.0)
    negative_pct: float = Field(default=0.0)
    error_message: str = Field(default="")

    @field_validator("platform", mode="before")
    @classmethod
    def _validate_platform(cls, v: str) -> str:
        return normalize_platform(v)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Raw Comments
# ─────────────────────────────────────────────────────────────────────────────


class RawComment(BaseModel):
    """Collected user comment before AI analysis."""

    comment_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    source_url: str = Field(..., description="URL this comment was scraped from")
    platform: PlatformType = Field(default="Other")
    author: str = Field(default="Anonymous")
    published_date: Optional[str] = Field(
        default=None, description="ISO date string or human-readable date"
    )
    rating: Optional[float] = Field(
        default=None, ge=0, le=5, description="Numeric rating 0-5 if available"
    )
    is_target: bool = Field(
        default=False, description="True if this comment was scraped from the primary targeted URL"
    )
    original_text: str = Field(..., min_length=1, description="Raw scraped comment text")

    @field_validator("original_text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("original_text must not be empty after stripping")
        return stripped

    @field_validator("platform", mode="before")
    @classmethod
    def _validate_platform(cls, v: str) -> str:
        return normalize_platform(v)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 — Analyzed Comments (LLM output)
# ─────────────────────────────────────────────────────────────────────────────

SentimentType = Literal["Positive", "Neutral", "Negative"]


class AnalyzedComment(BaseModel):
    """RawComment enriched with multilingual AI analysis."""

    # Inherited fields from RawComment (flattened for Excel serialization)
    comment_id: str
    source_url: str
    platform: PlatformType = "Other"
    author: str = "Anonymous"
    published_date: Optional[str] = None
    rating: Optional[float] = None
    is_target: bool = False
    original_text: str

    # LLM-added fields
    original_language: str = Field(
        default="English", description="ISO language name, e.g. 'Hindi', 'Spanish'"
    )
    english_text: str = Field(
        ..., description="Faithful English translation or cleaned version"
    )
    sentiment: SentimentType = Field(..., description="Overall sentiment toward topic")
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="LLM confidence in classification"
    )
    aspect_tag: str = Field(
        ...,
        description="1-2 word aspect, e.g. 'Quality', 'Price', 'Battery', 'Support'",
    )
    is_spam: bool = Field(
        default=False, description="True if promotional, bot, or off-topic"
    )
    spam_reason: str = Field(default="", description="Reason if is_spam=True")

    @field_validator("platform", mode="before")
    @classmethod
    def _validate_platform(cls, v: str) -> str:
        return normalize_platform(v)


class BatchAnalysisResult(BaseModel):
    """LLM structured output for one Map batch."""

    comments: list[AnalyzedComment] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 Reduce — Executive Review
# ─────────────────────────────────────────────────────────────────────────────


class AspectSummary(BaseModel):
    """Per-aspect aggregated stats."""

    aspect_name: str
    mention_count: int = 0
    positive_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    neutral_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    negative_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    key_takeaway: str = Field(default="")


class ExecutiveReview(BaseModel):
    """Final AI-generated executive summary produced by the Reduce LLM call."""

    model_config = ConfigDict(extra="allow")

    topic_name: str
    original_url: str
    overall_sentiment: SentimentType
    ai_score_out_of_10: float = Field(..., ge=0.0, le=10.0)
    executive_summary: str = Field(..., description="2-4 paragraph balanced overview")
    top_pros: list[str] = Field(..., min_length=1, max_length=8)
    top_cons: list[str] = Field(..., min_length=1, max_length=8)
    common_complaints: list[str] = Field(default_factory=list, max_length=8)
    platform_comparison_notes: str = Field(
        default="", description="Differences across Reddit, YouTube, blogs, etc."
    )
    buying_or_action_verdict: str = Field(
        ..., description="Who should buy/watch this and what to watch out for"
    )
    aspect_summaries: list[AspectSummary] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline State Container
# ─────────────────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    """Return current UTC time — compatible with Python 3.11 and 3.12+."""
    return datetime.now(tz=timezone.utc)


class PipelineState(BaseModel):
    """Full state of one pipeline run, passed between stages."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    input_url: str
    created_at: datetime = Field(default_factory=_utcnow)
    topic: Optional[TopicProfile] = None
    sources: list[DiscoveredSource] = Field(default_factory=list)
    raw_comments: list[RawComment] = Field(default_factory=list)
    analyzed_comments: list[AnalyzedComment] = Field(default_factory=list)
    executive_review: Optional[ExecutiveReview] = None
    output_xlsx_path: str = Field(default="")
    stage_errors: list[str] = Field(default_factory=list)
    # Computed stats for UI display
    total_comments_raw: int = Field(default=0)
    total_comments_valid: int = Field(default=0)
    positive_pct: float = Field(default=0.0)
    neutral_pct: float = Field(default=0.0)
    negative_pct: float = Field(default=0.0)
