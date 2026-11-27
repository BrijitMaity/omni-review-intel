"""
app.py — Premium Amazon-style Streamlit UI for omni-review-intel.

Launch with:
    streamlit run app.py

Design principles:
  - Amazon-inspired: clean white background, Amazon navy (#232F3E) & orange (#F0C14B/#FF9900)
  - Full session_state persistence across reruns and report downloads
  - Live 5-stage progress with detailed status log
  - Star-rating visual for AI score & Amazon-style customer review breakdown bars
  - "Customers say" AI overview card with highlight aspect tags
  - Side-by-side Pros & Cons cards
  - Tabbed results: Overview, Discovered Sources, Customer Reviews, Analytics
  - One-click .xlsx download with file size display
  - Runs pipeline in a background thread to avoid event loop conflicts in Streamlit
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
from html import escape as _h
from pathlib import Path
from typing import Any, Optional

import streamlit as st
from core.url_safety import is_safe_public_url as _is_safe_public_url
from core.ui_theme import (
    VIBRANT_SERIES_COLORS,
    build_theme_css,
    get_theme_palette,
    plotly_base_layout,
    sentiment_cell_style,
    status_cell_style,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Omni Review Intel — Review Intelligence Dashboard",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Light Mode is the default; Dark Mode activates cleanly via ui_theme_radio
_is_dark_theme = "Dark" in st.session_state.get("ui_theme_radio", "☀️ Light")
_theme = get_theme_palette(_is_dark_theme)
st.markdown(build_theme_css(_is_dark_theme), unsafe_allow_html=True)


# ── Session State Initialization ─────────────────────────────────────────────
if "analysis_state" not in st.session_state:
    st.session_state.analysis_state = None
if "multi_analysis_states" not in st.session_state:
    st.session_state.multi_analysis_states = []



# ── Helper: star rating HTML ──────────────────────────────────────────────────
def _stars_html(score: float, max_score: float = 10.0, num_stars: int = 5) -> str:
    filled = max(0, min(num_stars, round(score / max_score * num_stars)))
    return (
        '<span class="stars">'
        + "".join(
            f'<span class="star-filled">★</span>' if i < filled
            else f'<span class="star-empty">☆</span>'
            for i in range(num_stars)
        )
        + "</span>"
    )


def _sentiment_pill(sentiment: str) -> str:
    cls = {"Positive": "pill-pos", "Neutral": "pill-neu", "Negative": "pill-neg"}.get(
        sentiment, "pill-neu"
    )
    return f'<span class="{cls}">{_h(str(sentiment))}</span>'


def _load_cloud_secrets() -> None:
    """Populate server-wide settings from st.secrets once on hosted cloud platforms."""
    if st.session_state.get("_cloud_secrets_checked"):
        return
    st.session_state["_cloud_secrets_checked"] = True
    try:
        from config import settings as _s

        secret_keys = (
            "gemini_api_key",
            "openai_api_key",
            "youtube_api_key",
            "reddit_client_id",
            "reddit_client_secret",
            "tavily_api_key",
            "serpapi_key",
            "apify_api_key",
            "instagram_session_id",
            "amazon_cookie",
            "facebook_cookie",
        )
        raw_secrets = dict(st.secrets)
        llm_key_updated = False
        for field_name in secret_keys:
            cur = str(getattr(_s, field_name, "") or "").strip()
            if cur:
                continue
            val = raw_secrets.get(field_name.upper()) or raw_secrets.get(field_name) or ""
            if isinstance(val, str) and val.strip():
                setattr(_s, field_name, val.strip())
                if field_name in ("gemini_api_key", "openai_api_key"):
                    llm_key_updated = True
        if llm_key_updated:
            try:
                from stages._llm_client import get_llm_client
                get_llm_client(force_refresh=True)
            except Exception:
                pass
    except Exception:
        # Safe no-op when running locally without secrets.toml or inside restricted sandboxes
        pass


_load_cloud_secrets()


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="amazon-header">
      <h1>🛒 Omni Review Intel</h1>
      <p>Paste any product, video, or discussion link &mdash; we automatically collect customer reviews across Amazon, Flipkart, YouTube, Instagram, Facebook, Reddit &amp; the web and build vibrant analytics and Excel exports.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🛍️ Review Intel Hub")

    st.radio(
        "Theme Appearance",
        ["☀️ Light", "🌙 Dark"],
        horizontal=True,
        key="ui_theme_radio",
        help="Switch between bright Light Mode and high-contrast Dark Mode.",
    )

    # Friendly Reassurance Card
    st.markdown(
        """
        <div class="sidebar-info-card">
          <div class="sidebar-info-title">✨ 100% Ready to Use</div>
          <div class="sidebar-info-body">
            Paste any product or video link on the right to start. The system searches across platforms, analyzes feedback, and prepares interactive charts &amp; Excel reports.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### 🌐 Ready Sources")

    try:
        from config import settings as _s
        has_gem = bool(_s.has_gemini or st.session_state.get("user_gemini_key", "").strip())
        has_apify = bool(_s.has_apify)
        has_ig_full = bool(_s.has_instagram_session or st.session_state.get("user_ig_session", "").strip() or has_apify)
        has_amz_full = bool(_s.has_amazon_cookie or st.session_state.get("user_amazon_cookie", "").strip() or has_apify)
        has_fb_full = bool(_s.has_facebook_cookie or st.session_state.get("user_fb_cookie", "").strip())
    except Exception:
        has_gem = bool(st.session_state.get("user_gemini_key", "").strip())
        has_apify = False
        has_ig_full = bool(st.session_state.get("user_ig_session", "").strip())
        has_amz_full = bool(st.session_state.get("user_amazon_cookie", "").strip())
        has_fb_full = bool(st.session_state.get("user_fb_cookie", "").strip())

    _ok_c = _theme["badge_ok"]
    _warn_c = _theme["badge_warn"]
    source_badges = [
        ("🛒 Amazon", "✅ Full Access" if has_amz_full else "⚡ Public /dp/ Reviews", _ok_c if has_amz_full else _warn_c),
        ("🛍️ Flipkart", "✅ Paginated Ready", _ok_c),
        ("🎥 YouTube", "✅ Ready", _ok_c),
        ("💬 Reddit", "✅ Ready", _ok_c),
        ("📘 Facebook", "✅ Full Access" if has_fb_full else "⚡ Public Preview", _ok_c if has_fb_full else _warn_c),
        ("📸 Instagram", "✅ Full Access" if has_ig_full else "⚡ Public Preview", _ok_c if has_ig_full else _warn_c),
        ("🎵 TikTok & X", "✅ Ready", _ok_c),
        ("🌐 All Web & Forums", "✅ Ready", _ok_c),
        ("📊 Excel Exporter", "✅ Ready", _ok_c),
        ("🤖 Review Analyzer", "✅ AI Active" if has_gem else "⚡ Built-in Smart", _ok_c if has_gem else _warn_c),
    ]
    status_html = '<div style="display:flex; flex-direction:column; gap:6px; margin: 8px 0 16px 0;">'
    for name, status, color in source_badges:
        status_html += (
            f'<div class="source-badge-card">'
            f'<span class="source-badge-name">{name}</span>'
            f'<span style="color:{color}; font-size:0.82rem; font-weight:700;">{status}</span>'
            f'</div>'
        )
    status_html += '</div>'
    st.markdown(status_html, unsafe_allow_html=True)

    # Optional Settings Expander (cleanly tucks away technical parameters)
    with st.expander("⚙️ Optional Settings & AI Key", expanded=False):
        st.markdown(
            "<span class='sidebar-muted-note'>Default settings are already optimized. Adjust only if needed.</span>",
            unsafe_allow_html=True,
        )

        try:
            from config import settings
            _default_max_urls     = settings.max_discovered_urls
            _default_max_comments = settings.max_comments_per_url
        except Exception:
            _default_max_urls     = 15
            _default_max_comments = 5000

        max_urls = st.slider(
            "Websites to Search (Max Discovery URLs)",
            5, 30, _default_max_urls,
            help="Number of web sources and video links to scan."
        )
        collect_all_comments = st.checkbox(
            "Collect ALL Available Comments (Uncapped)",
            value=True,
            key="collect_all_comments_cb",
            help="Removes all comment and page caps across YouTube, Amazon, Flipkart, Instagram, TikTok, Reddit, and Web to gather every available comment & reply. Uncheck to apply the slider limit above."
        )
        max_comments = st.slider(
            "Comments per Website (Max Comments per URL)",
            100, 100000, min(max(100, _default_max_comments or 5000), 100000), step=500,
            disabled=collect_all_comments,
            help="Maximum customer comments to extract per source when Uncapped mode above is unchecked."
        )

        st.markdown("---")
        st.markdown("<span class='sidebar-field-label'>Gemini AI Key (Optional)</span>", unsafe_allow_html=True)
        user_gemini_key = st.text_input(
            "Gemini API Key",
            type="password",
            key="user_gemini_key",
            placeholder="Paste Gemini key...",
            help="Not required! The app works automatically out of the box using smart review analysis. If you have a Google Gemini key, you can optionally paste it here (isolated to your session only).",
            label_visibility="collapsed",
        )

        st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
        st.markdown("<span class='sidebar-field-label'>Instagram Session Cookie (Optional)</span>", unsafe_allow_html=True)
        user_ig_session = st.text_input(
            "Instagram Session Cookie",
            type="password",
            key="user_ig_session",
            placeholder="Paste IG sessionid...",
            help="Without login, Instagram limits public pages to the initial comment preview (~10-15 comments). Paste your browser's `sessionid` cookie (used only for your current session) to paginate all comments.",
            label_visibility="collapsed",
        )

        st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
        st.markdown("<span class='sidebar-field-label'>Amazon Cookie (Optional)</span>", unsafe_allow_html=True)
        user_amazon_cookie = st.text_input(
            "Amazon Cookie",
            type="password",
            key="user_amazon_cookie",
            placeholder="Paste Amazon Cookie...",
            help="Without sign-in, Amazon limits /product-reviews/ pages and exposes featured /dp/ reviews only. Paste your browser Cookie header (used only for your current session) to paginate all reviews.",
            label_visibility="collapsed",
        )

        st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
        st.markdown("<span class='sidebar-field-label'>Facebook Cookie (Optional)</span>", unsafe_allow_html=True)
        user_fb_cookie = st.text_input(
            "Facebook Cookie",
            type="password",
            key="user_fb_cookie",
            placeholder="Paste FB c_user/xs Cookie...",
            help="Without login, public Facebook posts return initial preview comments only. Paste your Cookie header (used only for your current session) for extended access.",
            label_visibility="collapsed",
        )

    st.markdown(
        "<div class='sidebar-footer'>Omni Review Intel &bull; Dual Theme Insights</div>",
        unsafe_allow_html=True,
    )


# ── Pipeline execution (thread-safe asyncio) ──────────────────────────────────
def _run_pipeline_sync(
    url: str,
    out_dir: str,
    progress_queue,
    overrides: Optional[dict[str, Any]] = None,
) -> dict:
    """Run the async pipeline in a fresh event loop inside a thread with session-scoped settings.
    Returns a dict with state data or {'error': str}.
    """
    async def _inner():
        from core.pipeline import run_pipeline

        async def _cb(stage: str, message: str) -> None:
            progress_queue.put_nowait((stage, message))

        return await run_pipeline(
            input_url=url,
            output_dir=out_dir,
            progress_cb=_cb,
        )

    try:
        from config import scoped_settings

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with scoped_settings(**(overrides or {})):
                state = loop.run_until_complete(_inner())
        finally:
            loop.close()
        return {"state": state}
    except Exception as e:
        return {"error": str(e)}


# ── URL Input Form ───────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="url-prompt-label">
      🔗 Paste any product, review video, or discussion link below:
    </div>
    """,
    unsafe_allow_html=True,
)
with st.form("run_form", clear_on_submit=False):
    col_input, col_btn = st.columns([5, 1])
    with col_input:
        url_input = st.text_input(
            "Input URL",
            placeholder=(
                "https://www.youtube.com/watch?v=...  |  "
                "https://amazon.in/dp/B09XYZ  |  "
                "https://flipkart.com/.../p/...  |  "
                "https://instagram.com/reel/..."
            ),
            label_visibility="collapsed",
        )
    with col_btn:
        submitted = st.form_submit_button(
            "🔍 Analyze",
            use_container_width=True,
            type="primary",
        )

# Clear / Reset action
col_spacer, col_clear = st.columns([5, 1])
with col_clear:
    if st.button("🔄 Clear / Reset", use_container_width=True):
        st.session_state.analysis_state = None
        st.session_state.multi_analysis_states = []
        st.rerun()


# ── Process Submission ────────────────────────────────────────────────────────
try:
    from config import settings as _settings
    output_dir = _settings.outputs_dir
except Exception:
    output_dir = "outputs"

session_overrides: dict[str, Any] = {
    "max_discovered_urls": max_urls,
    "max_comments_per_url": 0 if collect_all_comments else max_comments,
}
if user_gemini_key.strip():
    session_overrides["gemini_api_key"] = user_gemini_key.strip()
if user_ig_session.strip():
    session_overrides["instagram_session_id"] = user_ig_session.strip()
if user_amazon_cookie.strip():
    session_overrides["amazon_cookie"] = user_amazon_cookie.strip()
if user_fb_cookie.strip():
    session_overrides["facebook_cookie"] = user_fb_cookie.strip()

if submitted:
    import re
    raw_tokens = [tok.strip() for tok in re.split(r"[\r\n,]+|\s{2,}", url_input) if tok.strip()]
    candidate_urls = [
        (t if "://" in t else f"https://{t}")
        for t in raw_tokens
        if "." in t
    ]
    urls_to_process: list[str] = []
    unsafe_errors: list[str] = []
    for cand_url in candidate_urls:
        ok_url, reason = _is_safe_public_url(cand_url)
        if ok_url:
            urls_to_process.append(cand_url)
        else:
            unsafe_errors.append(reason)

    for err_msg in unsafe_errors:
        st.error(f"⛔ {err_msg}")

    if not urls_to_process:
        if not unsafe_errors:
            st.warning("⚠️ Please enter a URL before clicking Analyze.")
    else:
        # Inform user of analysis mode
        from config import settings as _settings
        has_llm = _settings.has_gemini or _settings.has_openai or bool(user_gemini_key.strip())
        if not has_llm:
            st.info("⚡ **Running in Smart Built-in Mode**: Automatically gathering reviews, detecting sentiments, and generating your Excel file without requiring an external AI API key.")

        # ── Progress UI ───────────────────────────────────────────────────────────
        st.markdown("---")
        st.markdown('<div class="section-title">🔄 Analysis Progress</div>', unsafe_allow_html=True)

        stage_progress = {
            "1": 0.0, "2": 0.20, "3": 0.40, "4": 0.70, "5": 0.95,
        }
        progress_bar = st.progress(0.0, text="Initializing pipeline…")
        log_placeholder = st.empty()

        import queue as queue_mod
        collected_states = []

        total_urls = len(urls_to_process)
        for u_idx, target_url in enumerate(urls_to_process, start=1):
            progress_queue: queue_mod.Queue = queue_mod.Queue()
            log_lines: list[str] = []
            result_container: list[dict] = []

            url_prefix = f"Target [{u_idx}/{total_urls}]" if total_urls > 1 else "Pipeline"

            def _thread_worker(cur_url=target_url, cur_overrides=dict(session_overrides)):
                res = _run_pipeline_sync(cur_url, output_dir, progress_queue, overrides=cur_overrides)
                result_container.append(res)
                progress_queue.put_nowait(("__DONE__", ""))

            thread = threading.Thread(target=_thread_worker, daemon=True)
            thread.start()

            while True:
                try:
                    stage, message = progress_queue.get(timeout=0.3)
                except queue_mod.Empty:
                    if not thread.is_alive() and progress_queue.empty():
                        break
                    continue

                if stage == "__DONE__":
                    break

                latest_stage, latest_msg = stage, message
                log_lines.append(f"[{url_prefix}] [{stage}] {message}")

                # Drain queued updates to avoid flooding Streamlit DOM on fast batch loops
                done_seen = False
                while not progress_queue.empty():
                    try:
                        s_next, m_next = progress_queue.get_nowait()
                    except queue_mod.Empty:
                        break
                    if s_next == "__DONE__":
                        done_seen = True
                        break
                    latest_stage, latest_msg = s_next, m_next
                    log_lines.append(f"[{url_prefix}] [{s_next}] {m_next}")

                log_text = _h("\n".join(log_lines[-20:]))
                log_placeholder.markdown(
                    f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True
                )

                m_stage = re.search(r"(\d+)", latest_stage)
                stage_num = m_stage.group(1) if m_stage else ""
                pct = stage_progress.get(stage_num, 0.0)
                if latest_msg.startswith(("Topic identified", "Discovered", "Collected", "Analysis complete", "Report ready")):
                    pct = stage_progress.get(str(int(stage_num) + 1) if stage_num.isdigit() else stage_num, pct)
                overall_pct = ((u_idx - 1) + min(pct + 0.05, 0.98)) / total_urls
                progress_bar.progress(overall_pct, text=f"[{url_prefix}] [{latest_stage}] {latest_msg[:80]}")

                if done_seen:
                    break

            thread.join()

            if result_container and "state" in result_container[0]:
                collected_states.append(result_container[0]["state"])
            elif result_container and "error" in result_container[0]:
                st.error(f"❌ Failed analyzing {target_url}: {result_container[0]['error']}")

        progress_bar.progress(1.0, text="✅ All analysis complete!")

        if not collected_states:
            st.error("❌ Pipeline failed to produce any reports.")
            st.stop()

        st.session_state.multi_analysis_states = collected_states
        st.session_state.analysis_state = collected_states[0]


# ════════════════════════════════════════════════════════════════════════════════
# RESULTS DISPLAY — Amazon Customer Review Standard
# ════════════════════════════════════════════════════════════════════════════════
if st.session_state.analysis_state is not None:
    # If multiple target links were analyzed, offer an unmixed switcher
    multi = getattr(st.session_state, "multi_analysis_states", [])
    if multi and len(multi) > 1:
        st.markdown(
            f"""
            <div class="multi-target-banner">
              <b>🎯 Multiple Target Links Processed ({len(multi)} Separate Reports Generated)</b>
              <p>Each targeted link was analyzed independently and has its own dedicated Excel report with zero mixing.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        target_names = [
            f"Target {i+1}: {s.topic.canonical_name if s.topic else 'Target'} — {s.input_url[:45]}..."
            for i, s in enumerate(multi)
        ]
        selected_idx = st.selectbox(
            "Select Target Report to View:",
            range(len(multi)),
            format_func=lambda i: target_names[i],
        )
        state = multi[selected_idx]
    else:
        state = st.session_state.analysis_state

    review = state.executive_review

    if not review:
        st.warning("Analysis produced no review data. Try a different URL.")
        st.stop()

    st.markdown("---")

    # ── Product Identity Banner ───────────────────────────────────────────────
    is_product = bool(state.topic and state.topic.input_type == "product")
    rating_unit = "global customer ratings" if is_product else "analyzed public comments"
    tgt_count = sum(1 for c in state.analyzed_comments if c.is_target)
    web_count = max(0, len(state.analyzed_comments) - tgt_count)
    split_note = (
        f" &nbsp;·&nbsp; <span class='meta-subtext'>Target URL: <b>{tgt_count:,}</b> | Web Discovery: <b>{web_count:,}</b></span>"
        if state.analyzed_comments
        else ""
    )

    id_col1, id_col2 = st.columns([3, 1])
    with id_col1:
        st.markdown(f"## {review.topic_name}")
        if state.topic:
            st.markdown(
                f"<span class='meta-subtext'>"
                f"<b>Brand:</b> {_h(state.topic.brand or 'N/A')} &nbsp;|&nbsp; "
                f"<b>Category:</b> {_h(state.topic.category or 'N/A')} &nbsp;|&nbsp; "
                f"<b>Type:</b> {_h(state.topic.input_type.upper())}"
                f"</span>",
                unsafe_allow_html=True,
            )
        st.markdown(
            f"{_stars_html(review.ai_score_out_of_10)}&nbsp;&nbsp;"
            f"<span class='rating-headline'>{review.ai_score_out_of_10:.1f} out of 10</span>&nbsp;&nbsp;"
            f"{_sentiment_pill(review.overall_sentiment)}&nbsp;&nbsp;"
            f"<span class='rating-count'>{state.total_comments_valid:,} {rating_unit}</span>{split_note}",
            unsafe_allow_html=True,
        )
    with id_col2:
        st.markdown(
            f'<div class="score-badge">'
            f'{review.ai_score_out_of_10:.1f}<br>'
            f'<small style="font-size:0.78rem;font-weight:600;color:rgba(255,255,255,0.82);letter-spacing:0.06em;">AI REVIEW SCORE</small></div>',
            unsafe_allow_html=True,
        )

    # ── KPI Cards ─────────────────────────────────────────────────────────────
    st.markdown("")
    k1, k2, k3, k4, k5 = st.columns(5)
    kpi_data = [
        (k1, "Sources Scanned",   str(len(state.sources))),
        (k2, "Raw Comments",      f"{state.total_comments_raw:,}"),
        (k3, "Valid Comments",    f"{state.total_comments_valid:,}"),
        (k4, "Positive Sentiment",f"{state.positive_pct:.1f}%"),
        (k5, "Negative Sentiment",f"{state.negative_pct:.1f}%"),
    ]
    for col, label, value in kpi_data:
        with col:
            st.markdown(
                f'<div class="kpi-card">'
                f'<div class="kpi-label">{label}</div>'
                f'<div class="kpi-value">{value}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # Surface target collection limits/blocks visibly right on the dashboard
    for _tsrc in state.sources:
        if _tsrc.is_target and _tsrc.scrape_status in ("partial", "failed") and _tsrc.error_message:
            st.warning(f"**Target {_tsrc.platform} Notice:** {_tsrc.error_message}")
            if _tsrc.platform == "Instagram":
                with st.expander("🔓 How to collect ALL comments from this Instagram post (15-second setup)"):
                    st.markdown(
                        "Instagram blocks anonymous scraping after ~8–12 preview comments (`rate limit` / login gate). "
                        "To fetch **all thousands of comments & replies**:\n"
                        "1. Open Instagram logged-in inside your browser and press **F12 → Application → Cookies → `https://www.instagram.com`**.\n"
                        "2. Copy the **`sessionid`** cookie value.\n"
                        "3. Open **Sidebar (left) → ⚙️ Optional Settings & AI Key → Instagram Session Cookie**, paste it (kept isolated in your session only), and click **🔍 Analyze** again."
                    )
    tab_overview, tab_sources, tab_comments, tab_analytics = st.tabs([
        "📊 Customer Review Overview",
        f"🌐 Discovered Sources ({len(state.sources)})",
        f"💬 Customer Reviews ({len(state.analyzed_comments):,})",
        "📈 Analytics & Charts",
    ])

    with tab_overview:
        col_meter, col_summary = st.columns([1, 2])

        with col_meter:
            st.markdown("### Customer Reviews")
            st.markdown(
                f"{_stars_html(review.ai_score_out_of_10)} "
                f"<span class='rating-headline'>{review.ai_score_out_of_10:.1f} out of 10</span>",
                unsafe_allow_html=True,
            )
            st.markdown(f"<span class='meta-subtext' style='font-size:0.85rem;'>{state.total_comments_valid:,} {rating_unit} analyzed</span>", unsafe_allow_html=True)
            st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

            # Review breakdown bars
            pos_pct = state.positive_pct
            neu_pct = state.neutral_pct
            neg_pct = state.negative_pct

            st.markdown(
                f"""
                <div class="meter-row">
                    <span class="meter-label">Positive</span>
                    <div class="meter-track"><div class="meter-fill-pos" style="width: {pos_pct}%;"></div></div>
                    <span class="meter-pct">{pos_pct:.0f}%</span>
                </div>
                <div class="meter-row">
                    <span class="meter-label">Neutral</span>
                    <div class="meter-track"><div class="meter-fill-neu" style="width: {neu_pct}%;"></div></div>
                    <span class="meter-pct">{neu_pct:.0f}%</span>
                </div>
                <div class="meter-row">
                    <span class="meter-label">Negative</span>
                    <div class="meter-track"><div class="meter-fill-neg" style="width: {neg_pct}%;"></div></div>
                    <span class="meter-pct">{neg_pct:.0f}%</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Interactive Plotly mini-donut (theme-synchronized)
            try:
                import plotly.graph_objects as go
                fig_mini = go.Figure(
                    data=[
                        go.Pie(
                            labels=["Positive", "Neutral", "Negative"],
                            values=[pos_pct, neu_pct, neg_pct],
                            marker=dict(
                                colors=[_theme["pos"], _theme["neu"], _theme["neg"]],
                                line=dict(color=_theme["bg_app"], width=2),
                            ),
                            hole=0.64,
                            textinfo="none",
                            hovertemplate="%{label}: %{value:.1f}%<extra></extra>",
                        )
                    ]
                )
                fig_mini.update_layout(
                    **plotly_base_layout(
                        _theme,
                        height=200,
                        showlegend=True,
                        legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center"),
                        margin=dict(l=4, r=4, t=8, b=28),
                        annotations=[
                            dict(
                                text=f"<b>{pos_pct:.0f}%</b><br><span style='font-size:11px'>Positive</span>",
                                x=0.5,
                                y=0.5,
                                font=dict(size=16, color=_theme["text_primary"]),
                                showarrow=False,
                            )
                        ],
                    )
                )
                st.plotly_chart(fig_mini, use_container_width=True)
            except Exception:
                pass

        with col_summary:
            # "Customers say" AI Review Insights Card
            chips_html = "".join(
                f'<span class="aspect-tag-chip">🏷️ {_h(a.aspect_name)} ({a.positive_pct:.0f}% positive)</span>'
                for a in review.aspect_summaries[:6]
            )
            st.markdown(
                f"""
                <div class="amazon-ai-card">
                  <div class="ai-header">
                    ✨ <b>Customers say</b>
                    <span class="ai-badge">Analyzed from {state.total_comments_valid:,} public reviews</span>
                  </div>
                  <p class="ai-summary-text">
                    {_h(review.executive_summary)}
                  </p>
                  <div>{chips_html}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Pros & Cons Side-by-Side Cards
            c_pro, c_con = st.columns(2)
            with c_pro:
                pro_bullets = "".join(f'<div class="pro-item">✔ {_h(p)}</div>' for p in review.top_pros)
                st.markdown(
                    f"""
                    <div class="pro-card">
                      <div class="pro-title">✅ Top Pros</div>
                      <div style="margin-top:8px;">{pro_bullets}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with c_con:
                con_bullets = "".join(f'<div class="con-item">✘ {_h(c)}</div>' for c in review.top_cons)
                complaints_html = ""
                if review.common_complaints:
                    complaints_html = (
                        "<div class='complaint-heading'>Frequent Complaints:</div>"
                        + "".join(f'<div class="complaint-item">▸ {_h(cp)}</div>' for cp in review.common_complaints[:3])
                    )
                st.markdown(
                    f"""
                    <div class="con-card">
                      <div class="con-title">❌ Points to Consider</div>
                      <div style="margin-top:8px;">{con_bullets}</div>
                      {complaints_html}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # Verdict / Recommendation Banner
        verdict_heading = "🏆 Actionable Buying Verdict" if is_product else "🏆 Actionable Content & Audience Verdict"
        st.markdown(
            f"""
            <div class="verdict-box">
              <div class="verdict-title">{verdict_heading}</div>
              <p class="verdict-body">{_h(review.buying_or_action_verdict)}</p>
              <span class="verdict-meta"><b>Platform Insights:</b> {_h(review.platform_comparison_notes or 'Consistent sentiment across all crawled platforms.')}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Aspect Summary Table
        if review.aspect_summaries:
            st.markdown('<div class="section-title">📊 Key Aspect Sentiment Breakdown</div>', unsafe_allow_html=True)
            import pandas as pd
            asp_df = pd.DataFrame(
                [
                    {
                        "Aspect":     a.aspect_name,
                        "Mentions":   a.mention_count,
                        "Positive %": f"{a.positive_pct:.1f}%",
                        "Neutral %":  f"{a.neutral_pct:.1f}%",
                        "Negative %": f"{a.negative_pct:.1f}%",
                        "Key Takeaway": a.key_takeaway,
                    }
                    for a in review.aspect_summaries
                ]
            )
            st.dataframe(asp_df, use_container_width=True, hide_index=True)

    with tab_sources:
        st.markdown("### Discovered Web & Community Sources")
        import pandas as pd
        sources_df = pd.DataFrame(
            [
                {
                    "Platform":  str(s.platform),
                    "Title":     (s.title or "")[:60],
                    "URL":       s.url[:70],
                    "Comments":  s.comments_collected,
                    "Positive":  f"{s.positive_pct:.1f}%",
                    "Negative":  f"{s.negative_pct:.1f}%",
                    "Status":    str(s.scrape_status),
                }
                for s in state.sources
            ]
        )

        def _status_color(val: str) -> str:
            return status_cell_style(val, _is_dark_theme)

        def _apply_style(styler, fn, subset):
            if hasattr(styler, "map"):
                return styler.map(fn, subset=subset)
            return styler.applymap(fn, subset=subset)

        styled_sources = _apply_style(sources_df.style, _status_color, subset=["Status"])
        st.dataframe(styled_sources, use_container_width=True, hide_index=True)

    with tab_comments:
        st.markdown(f"### Master Customer Reviews ({len(state.analyzed_comments):,})")
        import pandas as pd
        c_filter1, c_filter2 = st.columns([1, 2])
        with c_filter1:
            sent_filter = st.selectbox("Filter Sentiment", ["All", "Positive", "Neutral", "Negative"])
        with c_filter2:
            search_query = st.text_input("Search in reviews", placeholder="Search keyword (e.g. battery, bass, mic)...")

        filtered = state.analyzed_comments
        if sent_filter != "All":
            filtered = [c for c in filtered if c.sentiment == sent_filter]
        if search_query.strip():
            sq = search_query.strip().lower()
            filtered = [c for c in filtered if sq in c.english_text.lower() or sq in c.aspect_tag.lower()]

        st.caption(f"Showing all {len(filtered):,} matching comments (fully exported in Excel as well)")

        if filtered:
            comments_df = pd.DataFrame(
                [
                    {
                        "#":          idx,
                        "Scope":      "🎯 Target" if getattr(c, "is_target", False) else "🌐 Web",
                        "Platform":   str(c.platform),
                        "Sentiment":  c.sentiment,
                        "Aspect":     c.aspect_tag,
                        "Author":     c.author or "Anonymous",
                        "Date":       c.published_date or "—",
                        "Confidence": f"{c.confidence_score:.2f}",
                        "Comment":    (c.original_text or c.english_text or "")[:1000],
                    }
                    for idx, c in enumerate(filtered, start=1)
                ]
            )

            def _sent_color(val: str) -> str:
                return sentiment_cell_style(val, _is_dark_theme)

            if len(comments_df) <= 250:
                styled_comments = _apply_style(comments_df.style, _sent_color, subset=["Sentiment"])
                st.dataframe(styled_comments, use_container_width=True, hide_index=True, height=560)
            else:
                # Virtualized canvas DataFrame renders 5,000-50,000+ rows smoothly without Pandas Styler bottlenecks
                st.dataframe(comments_df, use_container_width=True, hide_index=True, height=600)

    with tab_analytics:
        st.markdown("### 📈 Vibrant Visual Intelligence Dashboard")
        a_col1, a_col2 = st.columns(2)
        try:
            import plotly.graph_objects as go
            from collections import defaultdict

            with a_col1:
                st.markdown("**Overall Sentiment Distribution**")
                fig1 = go.Figure(
                    data=[
                        go.Pie(
                            labels=["Positive", "Neutral", "Negative"],
                            values=[state.positive_pct, state.neutral_pct, state.negative_pct],
                            marker=dict(
                                colors=[_theme["pos"], _theme["neu"], _theme["neg"]],
                                line=dict(color=_theme["bg_app"], width=3),
                            ),
                            pull=[0.04, 0, 0],
                            hole=0.62,
                            textinfo="label+percent",
                            textfont=dict(color=_theme["text_primary"]),
                            hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
                        )
                    ]
                )
                fig1.update_layout(
                    **plotly_base_layout(
                        _theme,
                        height=350,
                        legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
                        annotations=[
                            dict(
                                text=f"<b>{review.ai_score_out_of_10:.1f}/10</b><br>{state.total_comments_valid:,} reviews",
                                x=0.5,
                                y=0.5,
                                font=dict(size=14, color=_theme["text_primary"]),
                                showarrow=False,
                            )
                        ],
                    )
                )
                st.plotly_chart(fig1, use_container_width=True)

            with a_col2:
                st.markdown("**Platform Volume & Sentiment Breakdown**")
                plat_counts: dict[str, dict[str, int]] = defaultdict(
                    lambda: {"Positive": 0, "Neutral": 0, "Negative": 0}
                )
                for c in state.analyzed_comments:
                    p_name = str(c.platform)
                    s_name = c.sentiment if c.sentiment in ("Positive", "Neutral", "Negative") else "Neutral"
                    plat_counts[p_name][s_name] += 1

                if plat_counts:
                    plats = sorted(
                        plat_counts.keys(),
                        key=lambda k: sum(plat_counts[k].values()),
                        reverse=True,
                    )
                    fig_plat = go.Figure(
                        data=[
                            go.Bar(
                                name="Positive",
                                x=plats,
                                y=[plat_counts[p]["Positive"] for p in plats],
                                marker=dict(color=_theme["pos"], line=dict(color=_theme["bg_app"], width=1)),
                            ),
                            go.Bar(
                                name="Neutral",
                                x=plats,
                                y=[plat_counts[p]["Neutral"] for p in plats],
                                marker=dict(color=_theme["neu"], line=dict(color=_theme["bg_app"], width=1)),
                            ),
                            go.Bar(
                                name="Negative",
                                x=plats,
                                y=[plat_counts[p]["Negative"] for p in plats],
                                marker=dict(color=_theme["neg"], line=dict(color=_theme["bg_app"], width=1)),
                            ),
                        ]
                    )
                    fig_plat.update_layout(
                        **plotly_base_layout(
                            _theme,
                            height=350,
                            barmode="stack",
                            margin=dict(l=16, r=16, t=24, b=48),
                            legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
                            yaxis_title="Comments",
                        )
                    )
                    st.plotly_chart(fig_plat, use_container_width=True)

            b_col1, b_col2 = st.columns(2)
            with b_col1:
                st.markdown("**Aspect Sentiment Breakdown (%)**")
                if review.aspect_summaries:
                    aspects = list(reversed(review.aspect_summaries[:8]))
                    asp_names = [a.aspect_name for a in aspects]
                    asp_pos = [a.positive_pct for a in aspects]
                    asp_neu = [a.neutral_pct for a in aspects]
                    asp_neg = [a.negative_pct for a in aspects]
                else:
                    asp_map: dict[str, dict[str, int]] = defaultdict(
                        lambda: {"Positive": 0, "Neutral": 0, "Negative": 0}
                    )
                    for c in state.analyzed_comments:
                        tag = (c.aspect_tag or "General").strip()
                        s_name = c.sentiment if c.sentiment in ("Positive", "Neutral", "Negative") else "Neutral"
                        asp_map[tag][s_name] += 1
                    top_tags = sorted(asp_map.keys(), key=lambda k: sum(asp_map[k].values()), reverse=True)[:8]
                    top_tags.reverse()
                    asp_names = top_tags
                    asp_pos, asp_neu, asp_neg = [], [], []
                    for t in top_tags:
                        tot = max(sum(asp_map[t].values()), 1)
                        asp_pos.append(round(asp_map[t]["Positive"] / tot * 100, 1))
                        asp_neu.append(round(asp_map[t]["Neutral"] / tot * 100, 1))
                        asp_neg.append(round(asp_map[t]["Negative"] / tot * 100, 1))

                if asp_names:
                    fig2 = go.Figure(
                        data=[
                            go.Bar(
                                name="Positive %",
                                y=asp_names,
                                x=asp_pos,
                                orientation="h",
                                marker_color=_theme["pos"],
                            ),
                            go.Bar(
                                name="Neutral %",
                                y=asp_names,
                                x=asp_neu,
                                orientation="h",
                                marker_color=_theme["neu"],
                            ),
                            go.Bar(
                                name="Negative %",
                                y=asp_names,
                                x=asp_neg,
                                orientation="h",
                                marker_color=_theme["neg"],
                            ),
                        ]
                    )
                    fig2.update_layout(
                        **plotly_base_layout(
                            _theme,
                            height=350,
                            barmode="stack",
                            margin=dict(l=16, r=16, t=24, b=48),
                            legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
                            xaxis_title="% of Mentions",
                            xaxis=dict(range=[0, 100], gridcolor=_theme["chart_grid"]),
                        )
                    )
                    st.plotly_chart(fig2, use_container_width=True)

            with b_col2:
                rated = [c.rating for c in state.analyzed_comments if c.rating is not None]
                top_src = sorted(
                    [s for s in state.sources if s.comments_collected > 0],
                    key=lambda s: s.comments_collected,
                    reverse=True,
                )[:8]

                if rated:
                    st.markdown("**Star Rating Distribution (1★ – 5★)**")
                    star_buckets = {i: 0 for i in range(1, 6)}
                    for r in rated:
                        bucket = max(1, min(5, round(float(r))))
                        star_buckets[bucket] += 1
                    fig_stars = go.Figure(
                        data=[
                            go.Bar(
                                x=[f"{s}★" for s in range(1, 6)],
                                y=[star_buckets[s] for s in range(1, 6)],
                                marker_color=["#EF4444", "#F97316", "#F59E0B", "#34D399", "#10B981"],
                                text=[star_buckets[s] for s in range(1, 6)],
                                textposition="auto",
                            )
                        ]
                    )
                    fig_stars.update_layout(
                        **plotly_base_layout(_theme, height=350, yaxis_title="Reviews")
                    )
                    st.plotly_chart(fig_stars, use_container_width=True)
                elif len(top_src) > 1:
                    st.markdown("**Comments Collected by Source**")
                    bar_colors = [VIBRANT_SERIES_COLORS[i % len(VIBRANT_SERIES_COLORS)] for i in range(len(top_src))]
                    fig_src = go.Figure(
                        data=[
                            go.Bar(
                                x=[f"{s.platform} ({s.domain or 'src'})"[:28] for s in top_src],
                                y=[s.comments_collected for s in top_src],
                                marker_color=bar_colors,
                                text=[s.comments_collected for s in top_src],
                                textposition="auto",
                            )
                        ]
                    )
                    fig_src.update_layout(
                        **plotly_base_layout(
                            _theme,
                            height=350,
                            margin=dict(l=16, r=16, t=24, b=52),
                            yaxis_title="Comments",
                        )
                    )
                    st.plotly_chart(fig_src, use_container_width=True)
                else:
                    st.markdown("**Sentiment Confidence Distribution**")
                    conf_buckets = {
                        "High (≥85%)": {"Positive": 0, "Neutral": 0, "Negative": 0},
                        "Medium (65–84%)": {"Positive": 0, "Neutral": 0, "Negative": 0},
                        "Moderate (<65%)": {"Positive": 0, "Neutral": 0, "Negative": 0},
                    }
                    for c in state.analyzed_comments:
                        tier = (
                            "High (≥85%)" if c.confidence_score >= 0.85
                            else ("Medium (65–84%)" if c.confidence_score >= 0.65 else "Moderate (<65%)")
                        )
                        s_name = c.sentiment if c.sentiment in ("Positive", "Neutral", "Negative") else "Neutral"
                        conf_buckets[tier][s_name] += 1
                    tiers = list(conf_buckets.keys())
                    fig_conf = go.Figure(
                        data=[
                            go.Bar(name="Positive", x=tiers, y=[conf_buckets[t]["Positive"] for t in tiers], marker_color=_theme["pos"]),
                            go.Bar(name="Neutral", x=tiers, y=[conf_buckets[t]["Neutral"] for t in tiers], marker_color=_theme["neu"]),
                            go.Bar(name="Negative", x=tiers, y=[conf_buckets[t]["Negative"] for t in tiers], marker_color=_theme["neg"]),
                        ]
                    )
                    fig_conf.update_layout(
                        **plotly_base_layout(
                            _theme,
                            height=350,
                            barmode="group",
                            margin=dict(l=16, r=16, t=24, b=48),
                            legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
                            yaxis_title="Comments",
                        )
                    )
                    st.plotly_chart(fig_conf, use_container_width=True)
        except ImportError:
            st.info("Install plotly for full interactive charts.")

    # ── Download Button ───────────────────────────────────────────────────────
    st.markdown("---")

    multi = getattr(st.session_state, "multi_analysis_states", [])
    if multi and len(multi) > 1:
        st.markdown("### 📥 Download Reports for Each Target Link")
        st.caption("Each targeted link has its own dedicated, unmixed Excel report:")
        for i, s in enumerate(multi, start=1):
            xp = Path(s.output_xlsx_path)
            if xp.exists():
                sz = xp.stat().st_size // 1024
                topic_title = s.topic.canonical_name if s.topic else f"Target {i}"
                st.download_button(
                    label=f"⬇️ Download Excel: Target {i} — {topic_title} ({sz} KB)",
                    data=xp.read_bytes(),
                    file_name=xp.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_target_{i}",
                    use_container_width=True,
                )
        st.markdown("---")

    xlsx_path = Path(state.output_xlsx_path)

    if xlsx_path.exists():
        file_size_kb = xlsx_path.stat().st_size // 1024
        dl_col1, dl_col2, dl_col3 = st.columns([1, 2, 1])
        with dl_col2:
            st.download_button(
                label=f"⬇️ Download Full Excel Report ({file_size_kb} KB)",
                data=xlsx_path.read_bytes(),
                file_name=xlsx_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            st.markdown(
                f"<p class='meta-subtext' style='text-align:center;font-size:0.85rem;margin-top:6px;'>"
                f"📄 {xlsx_path.name} &bull; Includes 9 sheets: All Reviews, Target Reviews, Good, Bad, Neutral, Discovered Web Reviews, Executive Review, Platform &amp; Aspect Summary, Sources</p>",
                unsafe_allow_html=True,
            )
    else:
        st.warning("Report file not found. Check the outputs/ directory.")

    if state.stage_errors:
        with st.expander("⚠️ Pipeline Warnings", expanded=False):
            for err in state.stage_errors:
                st.warning(err)
