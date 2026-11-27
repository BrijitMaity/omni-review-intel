"""
test_ui.py — Automated Streamlit UI test suite using streamlit.testing.v1.AppTest.

Tests UI behavior, default Light Mode, Dark Mode switching, vibrant Plotly analytics,
sidebar controls, state persistence, and large-comment table rendering.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def app_test():
    """Fixture to provide a clean AppTest instance pointing to app.py."""
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _sample_state(tmp_path, count: int = 20):
    from core.schemas import (
        AnalyzedComment,
        AspectSummary,
        DiscoveredSource,
        ExecutiveReview,
        PipelineState,
        TopicProfile,
    )
    from stages.s5_excel_builder import build_excel

    platforms = ["YouTube", "Instagram", "Amazon", "Flipkart", "Facebook"]
    comments = [
        AnalyzedComment(
            comment_id=f"c_{i}",
            source_url="https://youtube.com/watch?v=test",
            platform=platforms[i % len(platforms)],
            original_text=f"Sample review comment number {i}",
            english_text=f"Sample review comment number {i}",
            sentiment="Positive" if i % 2 == 0 else ("Negative" if i % 5 == 0 else "Neutral"),
            confidence_score=0.85,
            aspect_tag="Audio Quality" if i % 2 == 0 else "Battery Life",
            rating=5.0 if i % 2 == 0 else 3.0,
            is_target=True,
        )
        for i in range(count)
    ]
    review = ExecutiveReview(
        topic_name="Large Test Product",
        original_url="https://youtube.com/watch?v=test",
        overall_sentiment="Positive",
        ai_score_out_of_10=7.5,
        executive_summary="Strong reception across platforms.",
        top_pros=["Clear sound", "Long battery"],
        top_cons=["Premium price"],
        buying_or_action_verdict="Recommended",
        aspect_summaries=[
            AspectSummary(
                aspect_name="Audio Quality",
                mention_count=count // 2,
                positive_pct=85.0,
                neutral_pct=10.0,
                negative_pct=5.0,
                key_takeaway="Balanced crisp output.",
            ),
            AspectSummary(
                aspect_name="Battery Life",
                mention_count=count // 2,
                positive_pct=65.0,
                neutral_pct=20.0,
                negative_pct=15.0,
                key_takeaway="Lasts all day.",
            ),
        ],
    )
    sources = [
        DiscoveredSource(
            url="https://youtube.com/watch?v=test",
            platform="YouTube",
            scrape_status="success",
            comments_collected=count,
            is_target=True,
        )
    ]
    xlsx_path = build_excel(review, sources, comments, output_dir=str(tmp_path))
    return PipelineState(
        input_url="https://youtube.com/watch?v=test",
        topic=TopicProfile(
            canonical_name="Large Test Product",
            input_type="video",
            search_queries=["test", "test review", "test comments"],
        ),
        sources=sources,
        analyzed_comments=comments,
        executive_review=review,
        output_xlsx_path=xlsx_path,
        total_comments_raw=count,
        total_comments_valid=count,
        positive_pct=60.0,
        neutral_pct=20.0,
        negative_pct=20.0,
    )


def test_ui_theme_tokens_and_css_contrast():
    """Verify Light and Dark theme tokens produce valid high-contrast CSS and Plotly configs."""
    from core.ui_theme import build_theme_css, get_theme_palette, plotly_base_layout

    light_pal = get_theme_palette(is_dark=False)
    dark_pal = get_theme_palette(is_dark=True)
    assert light_pal["bg_app"] == "#F8FAFC"
    assert light_pal["chart_template"] == "plotly_white"
    assert dark_pal["bg_app"] == "#0F1117"
    assert dark_pal["chart_template"] == "plotly_dark"

    light_css = build_theme_css(is_dark=False)
    dark_css = build_theme_css(is_dark=True)
    assert "color-scheme: light;" in light_css
    assert "color-scheme: dark;" in dark_css
    assert "--neu-text:" in light_css
    assert "--neu_text" not in light_css

    layout = plotly_base_layout(dark_pal, height=350)
    assert layout["template"] == "plotly_dark"
    assert layout["font"]["color"] == dark_pal["text_primary"]


def test_ui_initial_render_light_mode_no_sample_demo(app_test):
    """Verify initial render defaults to Light Mode cleanly and has no sample demo controls."""
    at = app_test.run()
    assert len(at.exception) == 0, f"Exceptions on initial render: {at.exception}"

    assert at.session_state["analysis_state"] is None
    assert len(at.sidebar.radio) >= 1
    assert "Light" in at.sidebar.radio[0].value

    assert len(at.text_input) >= 1
    assert any("Input URL" in t.label for t in at.text_input)

    button_labels = [b.label for b in at.button]
    assert any("Analyze" in l for l in button_labels)
    assert any("Clear" in l for l in button_labels)
    assert not any("Load Sample" in l for l in button_labels)
    assert not any("Demo Mode" in c.label for c in at.sidebar.checkbox)

    slider_labels = [s.label for s in at.sidebar.slider]
    assert any("Max Discovery URLs" in l for l in slider_labels)
    assert any("Max Comments per URL" in l for l in slider_labels)


def test_ui_empty_url_warning(app_test):
    """Verify submitting an empty URL triggers a helpful warning without crashing."""
    at = app_test.run()
    analyze_btn = next(b for b in at.button if "Analyze" in b.label)
    analyze_btn.click().run()

    assert len(at.exception) == 0
    assert len(at.warning) >= 1
    assert "Please enter a URL" in at.warning[0].value
    assert at.session_state["analysis_state"] is None


def test_ui_renders_state_and_switches_light_dark_mode(app_test, tmp_path):
    """Verify populated dashboard + charts render cleanly in both Light Mode and Dark Mode."""
    at = app_test
    state = _sample_state(tmp_path, count=20)
    at.session_state["analysis_state"] = state
    at = at.run()

    assert len(at.exception) == 0
    assert len(at.download_button) >= 1
    assert os.path.exists(state.output_xlsx_path)

    # Toggle to Dark Mode cleanly via sidebar radio
    at.sidebar.radio[0].set_value("🌙 Dark")
    at = at.run(timeout=45)
    assert len(at.exception) == 0
    assert "Dark" in at.sidebar.radio[0].value

    # Toggle back to Light Mode cleanly
    at.sidebar.radio[0].set_value("☀️ Light")
    at = at.run(timeout=45)
    assert len(at.exception) == 0
    assert "Light" in at.sidebar.radio[0].value


def test_ui_clear_reset(app_test, tmp_path):
    """Verify Clear / Reset clears populated analysis_state and removes download button."""
    at = app_test
    at.session_state["analysis_state"] = _sample_state(tmp_path, count=10)
    at = at.run()
    assert len(at.download_button) >= 1

    reset_btn = next(b for b in at.button if "Clear" in b.label)
    reset_btn.click().run()

    assert len(at.exception) == 0
    assert at.session_state["analysis_state"] is None
    assert len(at.download_button) == 0


def test_ui_session_state_persistence(app_test, tmp_path):
    """Verify analysis_state persists across consecutive script reruns."""
    at = app_test
    at.session_state["analysis_state"] = _sample_state(tmp_path, count=10)
    at = at.run()
    assert at.session_state["analysis_state"] is not None

    at.run()
    assert len(at.exception) == 0
    assert at.session_state["analysis_state"] is not None
    assert len(at.download_button) >= 1


def test_ui_renders_over_1000_comments_without_truncation(app_test, tmp_path):
    """Verify GUI displays >1000 comments in dataframe without truncation or errors."""
    at = app_test
    at.session_state["analysis_state"] = _sample_state(tmp_path, count=1500)
    at = at.run()
    assert len(at.exception) == 0, f"Exceptions on large comment render: {at.exception}"
    assert any(len(df.value) == 1500 for df in at.dataframe), "Dataframe did not render all 1500 rows!"


def test_ui_blocks_unsafe_private_and_local_urls(app_test):
    """Ensure SSRF guard blocks local/private/metadata targets while allowing public web URLs."""
    from core.url_safety import is_safe_public_url

    for bad in (
        "http://127.0.0.1:8501",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/admin",
        "http://10.0.0.5/internal",
        "ftp://example.com/archive.zip",
    ):
        ok, _reason = is_safe_public_url(bad)
        assert not ok, f"Expected unsafe URL to be blocked: {bad}"

    for good in (
        "https://www.youtube.com/watch?v=k_mx4THsQik",
        "https://www.amazon.in/dp/B09XYZ1234",
        "https://www.instagram.com/p/DgMZ0QMTquh/",
    ):
        ok, reason = is_safe_public_url(good)
        assert ok, f"Expected public URL to be allowed ({good}): {reason}"

    at = app_test.run()
    url_widget = next(t for t in at.text_input if "Input URL" in t.label)
    url_widget.set_value("http://169.254.169.254/latest/meta-data/")
    analyze_btn = next(b for b in at.button if "Analyze" in b.label)
    analyze_btn.click().run()
    assert len(at.exception) == 0
    assert len(at.error) >= 1
    assert at.session_state["analysis_state"] is None


def test_ui_renders_zero_comment_state_and_escapes_html(app_test, tmp_path):
    """Ensure 0-comment fallback states and <script>/<tags> in review text render without errors or raw HTML tags."""
    state = _sample_state(tmp_path, count=0)
    state.executive_review = state.executive_review.model_copy(
        update={
            "executive_summary": "Users wrote <script>alert(1)</script> & <3 great audio!",
            "top_pros": ["<b>Crisp highs</b> & <3 bass"],
            "buying_or_action_verdict": "Check <img src=x onerror=alert(1)> safely",
        }
    )
    app_test.session_state["analysis_state"] = state
    at = app_test.run()
    assert len(at.exception) == 0
    md_joined = "\n".join(m.value for m in at.markdown)
    assert "<script>alert(1)</script>" not in md_joined
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in md_joined
    assert "<img src=x onerror=alert(1)>" not in md_joined


@pytest.mark.asyncio
async def test_scoped_settings_isolate_public_visitor_secrets():
    """Verify per-session keys/caps stay isolated inside scoped_settings without mutating globals."""
    import asyncio
    from config import has_scoped_override, scoped_settings, settings

    orig_ig = settings.instagram_session_id
    orig_max = settings.max_comments_per_url
    orig_gem_env = os.environ.get("GEMINI_API_KEY")

    with scoped_settings(
        instagram_session_id="visitor_ig_secret",
        gemini_api_key="visitor_gem_secret",
        max_comments_per_url=250,
    ):
        assert has_scoped_override("gemini_api_key") is True
        assert settings.instagram_session_id == "visitor_ig_secret"
        assert settings.has_instagram_session is True
        assert settings.gemini_api_key == "visitor_gem_secret"
        assert settings.has_gemini is True
        assert settings.max_comments_per_url == 250
        # Verify asyncio.to_thread propagates scoped_settings to blocking collectors
        assert await asyncio.to_thread(lambda: settings.max_comments_per_url) == 250
        assert await asyncio.to_thread(lambda: settings.instagram_session_id) == "visitor_ig_secret"

    assert has_scoped_override() is False
    assert settings.instagram_session_id == orig_ig
    assert settings.max_comments_per_url == orig_max
    assert os.environ.get("GEMINI_API_KEY") == orig_gem_env


def test_ui_helpers():
    """Directly test formatting helpers for star ratings and sentiment pills."""
    import sys

    pkg_dir = str(Path(__file__).resolve().parent.parent)
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)

    import app

    stars_10 = app._stars_html(10.0, max_score=10.0, num_stars=5)
    assert stars_10.count("★") == 5
    assert stars_10.count("☆") == 0

    stars_0 = app._stars_html(0.0, max_score=10.0, num_stars=5)
    assert stars_0.count("★") == 0
    assert stars_0.count("☆") == 5

    stars_half = app._stars_html(5.0, max_score=10.0, num_stars=5)
    assert stars_half.count("★") >= 2

    pos_pill = app._sentiment_pill("Positive")
    assert "pill-pos" in pos_pill
    assert "Positive" in pos_pill

    assert "pill-neu" in app._sentiment_pill("Neutral")
    assert "pill-neg" in app._sentiment_pill("Negative")
    assert "pill-neu" in app._sentiment_pill("Other")
