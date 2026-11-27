"""
core/ui_theme.py — Dual Light/Dark theme tokens, CSS system, and vibrant Plotly helpers.

Light Mode (default) uses an airy luminous alabaster canvas with pure white cards,
deep slate typography, cobalt-to-azure hero gradients, and vibrant emerald/coral/amber data colors.
Dark Mode uses layered obsidian/charcoal surfaces with subtle mist borders and glowing series accents.
"""

from __future__ import annotations

from typing import Any


def get_theme_palette(is_dark: bool) -> dict[str, str]:
    """Return harmonized semantic color tokens for Light or Dark UI mode."""
    if is_dark:
        return {
            "mode": "dark",
            "bg_app": "#0F1117",
            "bg_sidebar": "#161922",
            "bg_card": "#1E222D",
            "bg_card_alt": "#252A38",
            "bg_input": "#1A1E29",
            "bg_hover": "#2A3040",
            "text_primary": "#F8FAFC",
            "text_secondary": "#CBD5E1",
            "text_muted": "#94A3B8",
            "link_accent": "#38BDF8",
            "border_subtle": "rgba(255, 255, 255, 0.12)",
            "border_strong": "rgba(255, 255, 255, 0.22)",
            "shadow_card": "0 6px 20px rgba(0, 0, 0, 0.38)",
            "header_grad": "linear-gradient(135deg, #1E1B4B 0%, #312E81 55%, #0F172A 100%)",
            "header_border": "rgba(139, 92, 246, 0.45)",
            "score_bg": "linear-gradient(135deg, #1E1B4B 0%, #312E81 100%)",
            "score_fg": "#FBBF24",
            "accent_primary": "#F97316",
            "accent_secondary": "#8B5CF6",
            "pos": "#10B981",
            "pos_grad": "linear-gradient(90deg, #059669 0%, #34D399 100%)",
            "pos_bg": "rgba(16, 185, 129, 0.14)",
            "pos_border": "rgba(16, 185, 129, 0.40)",
            "pos_text": "#34D399",
            "neu": "#94A3B8",
            "neu_grad": "linear-gradient(90deg, #64748B 0%, #94A3B8 100%)",
            "neu_bg": "rgba(148, 163, 184, 0.16)",
            "neu_border": "rgba(148, 163, 184, 0.35)",
            "neu_text": "#CBD5E1",
            "neg": "#EF4444",
            "neg_grad": "linear-gradient(90deg, #DC2626 0%, #F87171 100%)",
            "neg_bg": "rgba(239, 68, 68, 0.14)",
            "neg_border": "rgba(239, 68, 68, 0.40)",
            "neg_text": "#FCA5A5",
            "warn_text": "#FBBF24",
            "warn_bg": "rgba(245, 158, 11, 0.15)",
            "warn_border": "rgba(245, 158, 11, 0.40)",
            "ai_card_bg": "linear-gradient(135deg, #1E222D 0%, #23283A 100%)",
            "ai_badge_bg": "rgba(56, 189, 248, 0.16)",
            "ai_badge_fg": "#38BDF8",
            "verdict_bg": "rgba(245, 158, 11, 0.10)",
            "verdict_border": "#F59E0B",
            "banner_bg": "rgba(59, 130, 246, 0.12)",
            "banner_border": "#3B82F6",
            "badge_ok": "#34D399",
            "badge_warn": "#FBBF24",
            "chart_grid": "rgba(255, 255, 255, 0.10)",
            "chart_template": "plotly_dark",
            "hover_bg": "#1E293B",
            "hover_fg": "#F8FAFC",
        }
    return {
        "mode": "light",
        "bg_app": "#F8FAFC",
        "bg_sidebar": "#F1F5F9",
        "bg_card": "#FFFFFF",
        "bg_card_alt": "#F8FAFC",
        "bg_input": "#FFFFFF",
        "bg_hover": "#E2E8F0",
        "text_primary": "#0F172A",
        "text_secondary": "#334155",
        "text_muted": "#64748B",
        "link_accent": "#0284C7",
        "border_subtle": "#E2E8F0",
        "border_strong": "#CBD5E1",
        "shadow_card": "0 4px 16px rgba(15, 23, 42, 0.06)",
        "header_grad": "linear-gradient(135deg, #1E3A8A 0%, #2563EB 52%, #0EA5E9 100%)",
        "header_border": "rgba(37, 99, 235, 0.25)",
        "score_bg": "linear-gradient(135deg, #1E3A8A 0%, #2563EB 100%)",
        "score_fg": "#FDE047",
        "accent_primary": "#F97316",
        "accent_secondary": "#2563EB",
        "pos": "#10B981",
        "pos_grad": "linear-gradient(90deg, #059669 0%, #10B981 100%)",
        "pos_bg": "#ECFDF5",
        "pos_border": "#A7F3D0",
        "pos_text": "#047857",
        "neu": "#64748B",
        "neu_grad": "linear-gradient(90deg, #64748B 0%, #94A3B8 100%)",
        "neu_bg": "#F1F5F9",
        "neu_border": "#CBD5E1",
        "neu_text": "#475569",
        "neg": "#EF4444",
        "neg_grad": "linear-gradient(90deg, #DC2626 0%, #EF4444 100%)",
        "neg_bg": "#FEF2F2",
        "neg_border": "#FECACA",
        "neg_text": "#B91C1C",
        "warn_text": "#B45309",
        "warn_bg": "#FFFBEB",
        "warn_border": "#FDE68A",
        "ai_card_bg": "linear-gradient(135deg, #FFFFFF 0%, #F0F9FF 100%)",
        "ai_badge_bg": "#E0F2FE",
        "ai_badge_fg": "#0369A1",
        "verdict_bg": "#FFFBEB",
        "verdict_border": "#F59E0B",
        "banner_bg": "#EFF6FF",
        "banner_border": "#3B82F6",
        "badge_ok": "#059669",
        "badge_warn": "#D97706",
        "chart_grid": "#E2E8F0",
        "chart_template": "plotly_white",
        "hover_bg": "#FFFFFF",
        "hover_fg": "#0F172A",
    }


VIBRANT_SERIES_COLORS: list[str] = [
    "#3B82F6",  # Electric Blue
    "#8B5CF6",  # Vivid Violet
    "#06B6D4",  # Bright Cyan
    "#F97316",  # Vibrant Tangerine
    "#10B981",  # Emerald
    "#EC4899",  # Vivid Pink
    "#F59E0B",  # Amber Gold
    "#6366F1",  # Indigo
]


def build_theme_css(is_dark: bool) -> str:
    """Build full responsive Light/Dark stylesheet overriding Streamlit surfaces cleanly."""
    p = get_theme_palette(is_dark)
    color_scheme = "dark" if is_dark else "light"
    return f"""
<style>
:root {{
    color-scheme: {color_scheme};
    --bg-app: {p['bg_app']};
    --bg-sidebar: {p['bg_sidebar']};
    --bg-card: {p['bg_card']};
    --bg-card-alt: {p['bg_card_alt']};
    --bg-input: {p['bg_input']};
    --bg-hover: {p['bg_hover']};
    --text-primary: {p['text_primary']};
    --text-secondary: {p['text_secondary']};
    --text-muted: {p['text_muted']};
    --link-accent: {p['link_accent']};
    --border-subtle: {p['border_subtle']};
    --border-strong: {p['border_strong']};
    --shadow-card: {p['shadow_card']};
    --accent-primary: {p['accent_primary']};
    --accent-secondary: {p['accent_secondary']};
    --pos-text: {p['pos_text']};
    --pos-bg: {p['pos_bg']};
    --pos-border: {p['pos_border']};
    --neu-text: {p['neu_text']};
    --neu-bg: {p['neu_bg']};
    --neu-border: {p['neu_border']};
    --neg-text: {p['neg_text']};
    --neg-bg: {p['neg_bg']};
    --neg-border: {p['neg_border']};
}}

/* ---- Global App Shell & Native Text ---- */
html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"], .main {{
    background-color: var(--bg-app) !important;
    color: var(--text-primary) !important;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {{
    background-color: var(--bg-app) !important;
    color: var(--text-primary) !important;
}}
[data-testid="stAppViewContainer"] h1,
[data-testid="stAppViewContainer"] h2,
[data-testid="stAppViewContainer"] h3,
[data-testid="stAppViewContainer"] h4,
[data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] label,
[data-testid="stMarkdownContainer"] {{
    color: var(--text-primary);
}}
[data-testid="stCaptionContainer"], .stCaption {{
    color: var(--text-muted) !important;
}}

/* ---- Hero Header ---- */
.amazon-header {{
    background: {p['header_grad']};
    color: #FFFFFF !important;
    padding: 22px 28px;
    border-radius: 14px;
    margin-bottom: 18px;
    border: 1px solid {p['header_border']};
    box-shadow: 0 8px 24px rgba(15, 23, 42, 0.14);
    border-bottom: 4px solid #F97316;
}}
.amazon-header h1 {{
    color: #FFFFFF !important;
    margin: 0;
    font-size: 1.85rem;
    font-weight: 800;
    letter-spacing: -0.02em;
}}
.amazon-header p {{
    color: rgba(255, 255, 255, 0.90) !important;
    margin: 6px 0 0 0;
    font-size: 0.96rem;
    line-height: 1.5;
}}

/* ---- Primary Form & Action Buttons ---- */
div[data-testid="stFormSubmitButton"] > button,
div[data-testid="stButton"] > button[kind="primary"] {{
    background: linear-gradient(135deg, #F97316 0%, #EA580C 100%) !important;
    color: #FFFFFF !important;
    border: 1px solid #EA580C !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    font-size: 0.98rem !important;
    padding: 10px 22px !important;
    box-shadow: 0 3px 10px rgba(249, 115, 22, 0.28) !important;
    transition: all 0.18s ease !important;
    cursor: pointer !important;
}}
div[data-testid="stFormSubmitButton"] > button:hover,
div[data-testid="stButton"] > button[kind="primary"]:hover {{
    background: linear-gradient(135deg, #FB923C 0%, #F97316 100%) !important;
    box-shadow: 0 5px 14px rgba(249, 115, 22, 0.40) !important;
}}
div[data-testid="stFormSubmitButton"] > button p,
div[data-testid="stButton"] > button[kind="primary"] p {{
    color: #FFFFFF !important;
}}
div[data-testid="stButton"] > button[kind="secondary"] {{
    background-color: var(--bg-card) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    transition: all 0.18s ease !important;
    cursor: pointer !important;
}}
div[data-testid="stButton"] > button[kind="secondary"]:hover {{
    border-color: var(--accent-primary) !important;
    background-color: var(--bg-hover) !important;
}}

/* ---- Main Inputs, Selectboxes & Tabs ---- */
.main [data-testid="stTextInputRootElement"],
.main [data-baseweb="input"],
.main [data-baseweb="select"] > div {{
    background-color: var(--bg-input) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 8px !important;
}}
.main [data-baseweb="base-input"] {{
    background-color: transparent !important;
    border: none !important;
}}
.main [data-testid="stTextInputRootElement"]:focus-within,
.main [data-baseweb="input"]:focus-within {{
    border-color: #F97316 !important;
    box-shadow: 0 0 0 2px rgba(249, 115, 22, 0.20) !important;
}}
.main input {{
    color: var(--text-primary) !important;
    -webkit-text-fill-color: var(--text-primary) !important;
    background-color: transparent !important;
}}
.main input::placeholder {{
    color: var(--text-muted) !important;
    -webkit-text-fill-color: var(--text-muted) !important;
}}
[data-testid="stTabs"] [role="tablist"] {{
    border-bottom: 2px solid var(--border-subtle) !important;
    gap: 6px;
}}
[data-testid="stTabs"] button[role="tab"] {{
    color: var(--text-secondary) !important;
    background-color: transparent !important;
    font-weight: 600 !important;
    border-radius: 8px 8px 0 0 !important;
    padding: 8px 14px !important;
}}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{
    color: #F97316 !important;
    border-bottom-color: #F97316 !important;
    background-color: var(--bg-card) !important;
}}

/* ---- Typography Helpers ---- */
.url-prompt-label {{
    margin-bottom: 8px;
    font-weight: 700;
    color: var(--text-primary) !important;
    font-size: 1.02rem;
}}
.meta-subtext {{
    color: var(--text-secondary) !important;
    font-size: 0.94rem;
}}
.rating-headline {{
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-primary) !important;
}}
.rating-count {{
    color: var(--link-accent) !important;
    font-weight: 600;
}}

/* ---- Score Badge & KPI Cards ---- */
.score-badge {{
    background: {p['score_bg']};
    color: {p['score_fg']} !important;
    font-size: 2.25rem;
    font-weight: 800;
    border-radius: 12px;
    padding: 14px 24px;
    text-align: center;
    display: inline-block;
    border: 1px solid {p['header_border']};
    box-shadow: var(--shadow-card);
}}
.kpi-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-top: 3px solid var(--accent-secondary);
    border-radius: 12px;
    padding: 15px 16px;
    text-align: center;
    box-shadow: var(--shadow-card);
    transition: transform 0.18s ease, border-color 0.18s ease;
}}
.kpi-card:hover {{
    border-top-color: #F97316;
}}
.kpi-label {{
    font-size: 0.74rem;
    color: var(--text-muted) !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 700;
}}
.kpi-value {{
    font-size: 1.55rem;
    font-weight: 800;
    color: var(--text-primary) !important;
    margin-top: 4px;
}}

/* ---- Stars & Sentiment Pills ---- */
.stars {{ font-size: 1.4rem; letter-spacing: 2px; }}
.star-filled {{ color: #F59E0B; }}
.star-empty {{ color: var(--border-strong); }}
.pill-pos {{
    background: var(--pos-bg); color: var(--pos-text);
    border: 1px solid var(--pos-border); border-radius: 999px;
    padding: 3px 12px; font-size: 0.84rem; font-weight: 700; display: inline-block;
}}
.pill-neu {{
    background: var(--neu-bg); color: var(--neu-text);
    border: 1px solid var(--neu-border); border-radius: 999px;
    padding: 3px 12px; font-size: 0.84rem; font-weight: 700; display: inline-block;
}}
.pill-neg {{
    background: var(--neg-bg); color: var(--neg-text);
    border: 1px solid var(--neg-border); border-radius: 999px;
    padding: 3px 12px; font-size: 0.84rem; font-weight: 700; display: inline-block;
}}

/* ---- Sentiment Meter Bars ---- */
.meter-row {{ display: flex; align-items: center; margin: 7px 0; font-size: 0.90rem; }}
.meter-label {{ width: 74px; color: var(--text-secondary); font-weight: 600; }}
.meter-track {{
    flex: 1; height: 16px; background: var(--bg-card-alt);
    border-radius: 999px; margin: 0 12px; overflow: hidden;
    border: 1px solid var(--border-subtle);
}}
.meter-fill-pos {{ height: 100%; background: {p['pos_grad']}; border-radius: 999px; }}
.meter-fill-neu {{ height: 100%; background: {p['neu_grad']}; border-radius: 999px; }}
.meter-fill-neg {{ height: 100%; background: {p['neg_grad']}; border-radius: 999px; }}
.meter-pct {{ width: 46px; text-align: right; color: var(--text-primary); font-weight: 700; }}

/* ---- AI Overview, Pros/Cons & Verdict Cards ---- */
.amazon-ai-card {{
    background: {p['ai_card_bg']};
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    padding: 18px 22px;
    margin: 0 0 14px 0;
    box-shadow: var(--shadow-card);
}}
.ai-header {{ font-size: 1.05rem; font-weight: 700; color: var(--text-primary); margin-bottom: 8px; }}
.ai-summary-text {{ font-size: 0.95rem; line-height: 1.6; color: var(--text-primary) !important; margin-bottom: 12px; }}
.ai-badge {{
    background: {p['ai_badge_bg']};
    color: {p['ai_badge_fg']};
    font-size: 0.75rem;
    padding: 3px 10px;
    border-radius: 999px;
    margin-left: 8px;
    font-weight: 700;
}}
.aspect-tag-chip {{
    display: inline-block;
    background: var(--bg-card);
    border: 1px solid var(--border-strong);
    border-radius: 999px;
    padding: 4px 12px;
    margin: 4px 4px 4px 0;
    font-size: 0.82rem;
    color: var(--text-primary);
    font-weight: 600;
}}
.pro-card {{
    background: var(--pos-bg);
    border: 1px solid var(--pos-border);
    border-radius: 12px;
    padding: 16px;
    height: 100%;
}}
.con-card {{
    background: var(--neg-bg);
    border: 1px solid var(--neg-border);
    border-radius: 12px;
    padding: 16px;
    height: 100%;
}}
.pro-title {{ color: var(--pos-text) !important; font-size: 1rem; font-weight: 700; }}
.con-title {{ color: var(--neg-text) !important; font-size: 1rem; font-weight: 700; }}
.pro-item {{ color: var(--pos-text); padding: 4px 0; font-size: 0.92rem; font-weight: 500; }}
.con-item {{ color: var(--neg-text); padding: 4px 0; font-size: 0.92rem; font-weight: 500; }}
.complaint-heading {{ margin-top: 10px; font-size: 0.85rem; font-weight: 700; color: {p['warn_text']}; }}
.complaint-item {{ color: {p['warn_text']}; padding: 3px 0; font-size: 0.90rem; }}
.verdict-box {{
    background: {p['verdict_bg']};
    border-left: 5px solid {p['verdict_border']};
    border-radius: 10px;
    padding: 14px 18px;
    margin: 14px 0;
    border-top: 1px solid var(--border-subtle);
    border-right: 1px solid var(--border-subtle);
    border-bottom: 1px solid var(--border-subtle);
}}
.verdict-title {{ color: var(--text-primary) !important; font-size: 1.05rem; font-weight: 700; }}
.verdict-body {{ margin: 6px 0 0 0; color: var(--text-primary) !important; font-size: 0.95rem; line-height: 1.55; }}
.verdict-meta {{ color: var(--text-secondary) !important; display: block; margin-top: 6px; font-size: 0.84rem; }}
.multi-target-banner {{
    background: {p['banner_bg']};
    border: 1.5px solid {p['banner_border']};
    border-radius: 10px;
    padding: 12px 18px;
    margin: 12px 0;
}}
.multi-target-banner b {{ color: var(--link-accent); font-size: 1.05rem; }}
.multi-target-banner p {{ margin: 4px 0 0 0; color: var(--text-secondary); font-size: 0.90rem; }}

/* ---- Progress Log & Section Titles ---- */
.log-box {{
    background: #0F172A;
    color: #34D399;
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 0.82rem;
    padding: 12px 16px;
    border-radius: 8px;
    max-height: 200px;
    overflow-y: auto;
    white-space: pre-wrap;
    border: 1px solid var(--border-subtle);
}}
.section-title {{
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-primary) !important;
    border-bottom: 2px solid #F97316;
    padding-bottom: 6px;
    margin: 20px 0 12px 0;
}}

/* ---- Download Button ---- */
div[data-testid="stDownloadButton"] > button {{
    background: linear-gradient(135deg, #F97316 0%, #EA580C 100%) !important;
    color: #FFFFFF !important;
    border: 1px solid #EA580C !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    font-size: 1.02rem !important;
    padding: 12px 24px !important;
    box-shadow: 0 3px 12px rgba(249, 115, 22, 0.30) !important;
    width: 100%;
    cursor: pointer !important;
}}
div[data-testid="stDownloadButton"] > button:hover {{
    background: linear-gradient(135deg, #FB923C 0%, #F97316 100%) !important;
}}
div[data-testid="stDownloadButton"] > button * {{
    color: #FFFFFF !important;
}}

/* ---- Sidebar Cohesive Theme Styling ---- */
[data-testid="stSidebar"] {{
    background-color: var(--bg-sidebar) !important;
    border-right: 1px solid var(--border-subtle) !important;
}}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div {{
    color: var(--text-primary);
}}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {{
    color: var(--text-primary) !important;
}}
.sidebar-info-card {{
    background: var(--bg-card);
    padding: 12px 14px;
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
    border-left: 4px solid #3B82F6;
    box-shadow: var(--shadow-card);
    margin: 10px 0 14px 0;
}}
.sidebar-info-title {{
    color: var(--accent-secondary);
    font-size: 0.94rem;
    font-weight: 700;
    margin-bottom: 4px;
}}
.sidebar-info-body {{
    color: var(--text-secondary);
    font-size: 0.83rem;
    line-height: 1.45;
}}
.source-badge-card {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--bg-card);
    padding: 7px 12px;
    border-radius: 8px;
    border: 1px solid var(--border-subtle);
}}
.source-badge-name {{
    color: var(--text-primary) !important;
    font-size: 0.85rem;
    font-weight: 600;
}}
.sidebar-field-label {{
    color: var(--text-primary) !important;
    font-size: 0.85rem;
    font-weight: 700;
    display: block;
    margin-bottom: 4px;
}}
.sidebar-muted-note {{
    color: var(--text-muted) !important;
    font-size: 0.80rem;
}}
.sidebar-footer {{
    text-align: center;
    margin-top: 22px;
    color: var(--text-muted) !important;
    font-size: 0.78rem;
}}

/* Sidebar Inputs & Password Eye Toggle Reset */
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"],
[data-testid="stSidebar"] [data-baseweb="input"] {{
    background-color: var(--bg-input) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}}
[data-testid="stSidebar"] [data-baseweb="base-input"] {{
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
}}
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stSidebar"] [data-baseweb="input"]:focus-within {{
    border-color: #F97316 !important;
    box-shadow: 0 0 0 1px #F97316 !important;
}}
[data-testid="stSidebar"] input {{
    color: var(--text-primary) !important;
    background-color: transparent !important;
    -webkit-text-fill-color: var(--text-primary) !important;
    font-size: 0.85rem !important;
    padding: 8px 10px !important;
}}
[data-testid="stSidebar"] input::placeholder {{
    color: var(--text-muted) !important;
    -webkit-text-fill-color: var(--text-muted) !important;
    opacity: 1 !important;
}}
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] button,
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] button,
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"] button,
[data-testid="stSidebar"] [data-baseweb="input"] button,
[data-testid="stSidebar"] [data-testid="stTextInput"] button {{
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    width: auto !important;
    min-width: unset !important;
    min-height: unset !important;
    height: auto !important;
    padding: 4px 6px !important;
    margin: 0 !important;
    cursor: pointer !important;
}}
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg,
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] svg {{
    stroke: var(--text-muted) !important;
    color: var(--text-muted) !important;
    width: 16px !important;
    height: 16px !important;
}}
[data-testid="stSidebar"] [data-testid="stTooltipIcon"]:hover svg,
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"]:hover svg {{
    stroke: #F97316 !important;
    color: #F97316 !important;
}}
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"] button svg,
[data-testid="stSidebar"] [data-baseweb="input"] button svg {{
    fill: var(--text-secondary) !important;
    color: var(--text-secondary) !important;
    width: 18px !important;
    height: 18px !important;
}}
[data-testid="stSidebar"] [data-testid="stTextInputRootElement"] button:hover svg,
[data-testid="stSidebar"] [data-baseweb="input"] button:hover svg {{
    fill: #F97316 !important;
    color: #F97316 !important;
}}

/* Sidebar Expander */
[data-testid="stSidebar"] details,
[data-testid="stSidebar"] [data-testid="stExpander"] {{
    background-color: var(--bg-card) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: 10px !important;
    margin: 10px 0 !important;
}}
[data-testid="stSidebar"] details summary,
[data-testid="stSidebar"] [data-testid="stExpander"] summary {{
    background-color: var(--bg-card) !important;
    color: var(--text-primary) !important;
    font-weight: 700 !important;
    font-size: 0.92rem !important;
    border-radius: 10px !important;
    padding: 9px 12px !important;
}}
[data-testid="stSidebar"] details summary:hover,
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {{
    background-color: var(--bg-hover) !important;
}}

/* Explicit Sidebar Action Buttons Only */
[data-testid="stSidebar"] div[data-testid="stButton"] > button,
[data-testid="stSidebar"] .stButton > button {{
    background: var(--bg-card) !important;
    color: var(--text-primary) !important;
    border: 1.5px solid #F97316 !important;
    border-radius: 8px !important;
    padding: 9px 14px !important;
    width: 100% !important;
    min-height: 40px !important;
    font-weight: 700 !important;
    cursor: pointer !important;
}}

@media (prefers-reduced-motion: reduce) {{
    * {{ transition: none !important; animation: none !important; }}
}}
</style>
"""


def plotly_base_layout(
    palette: dict[str, str],
    height: int = 340,
    **extra: Any,
) -> dict[str, Any]:
    """Build synchronized Light/Dark Plotly layout options with vibrant legibility."""
    layout: dict[str, Any] = {
        "template": palette["chart_template"],
        "height": height,
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": palette["text_primary"], "family": "Inter, Segoe UI, sans-serif", "size": 12},
        "hoverlabel": {"bgcolor": palette["hover_bg"], "font": {"color": palette["hover_fg"]}},
        "margin": {"l": 16, "r": 16, "t": 24, "b": 36},
        "xaxis": {"gridcolor": palette["chart_grid"], "zerolinecolor": palette["chart_grid"]},
        "yaxis": {"gridcolor": palette["chart_grid"], "zerolinecolor": palette["chart_grid"]},
    }
    layout.update(extra)
    return layout


def status_cell_style(val: str, is_dark: bool) -> str:
    """Theme-aware DataFrame cell colors for Discovered Sources statuses."""
    p = get_theme_palette(is_dark)
    mapping = {
        "success": f"background-color: {p['pos_bg']}; color: {p['pos_text']}; font-weight: 600",
        "partial": f"background-color: {p['warn_bg']}; color: {p['warn_text']}; font-weight: 600",
        "failed":  f"background-color: {p['neg_bg']}; color: {p['neg_text']}; font-weight: 600",
        "skipped": f"background-color: {p['neu_bg']}; color: {p['neu_text']}; font-weight: 600",
    }
    return mapping.get(val, "")


def sentiment_cell_style(val: str, is_dark: bool) -> str:
    """Theme-aware DataFrame cell colors for Comment sentiment tags."""
    p = get_theme_palette(is_dark)
    mapping = {
        "Positive": f"background-color: {p['pos_bg']}; color: {p['pos_text']}; font-weight: 600",
        "Negative": f"background-color: {p['neg_bg']}; color: {p['neg_text']}; font-weight: 600",
        "Neutral":  f"background-color: {p['neu_bg']}; color: {p['neu_text']}; font-weight: 600",
    }
    return mapping.get(val, "")
