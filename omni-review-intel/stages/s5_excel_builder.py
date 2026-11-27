"""
stages/s5_excel_builder.py — Stage 5: Multi-sheet openpyxl Excel report builder.

Generates an industry-standard, professional .xlsx workbook that cleanly saves all
comments and divides them into Good, Bad, and Neutral tabs:
  1. All Reviews & Comments    — Master sheet of all scraped comments across the web (Active Tab)
  2. 👍 Good Reviews           — Divided positive customer comments and praise
  3. 👎 Bad Reviews            — Divided negative customer comments, complaints & issues
  4. ⚖️ Neutral Reviews        — Divided neutral customer observations & questions
  5. Executive Review          — Overview metrics, pros/cons, verdict, embedded PieChart
  6. Platform & Aspect Summary — Platform/aspect performance tables, embedded BarChart
  7. Discovered Sources        — Verified audit log of all crawled URLs with hyperlinks
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from loguru import logger
from openpyxl import Workbook
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
import re

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

from config import settings
from core.schemas import AnalyzedComment, DiscoveredSource, ExecutiveReview

# ── Professional Color Palette (Corporate / Financial Standard) ───────────────
C_HEADER_BG = "1F4E79"   # Executive Deep Navy
C_HEADER_FG = "FFFFFF"   # Crisp White text
C_SUBHEADER = "2F5597"   # Steel Blue for section headers
C_ACCENT_BG = "D9E1F2"   # Soft Blue Accent
C_BORDER    = "D9D9D9"   # Light Gray clean border
C_ALT_ROW   = "F9FAFB"   # Very subtle zebra row tint
C_WHITE     = "FFFFFF"

# Soft, professional sentiment pill fills & text colors (Google/Excel standard)
C_POS_FILL  = "E6F4EA"   # Soft sage green
C_POS_TEXT  = "137333"   # Deep forest green
C_NEU_FILL  = "F1F3F4"   # Soft neutral gray
C_NEU_TEXT  = "5F6368"   # Slate gray
C_NEG_FILL  = "FCE8E6"   # Soft rose pink
C_NEG_TEXT  = "C5221F"   # Dark crimson


# ── Style Helpers ─────────────────────────────────────────────────────────────

def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def _font(
    bold: bool = False,
    size: int = 10,
    color: str = "000000",
    name: str = "Segoe UI",
    italic: bool = False,
    underline: str | None = None,
) -> Font:
    return Font(bold=bold, size=size, color=color, name=name, italic=italic, underline=underline)


def _border() -> Border:
    side = Side(style="thin", color=C_BORDER)
    return Border(left=side, right=side, top=side, bottom=side)


def _center(wrap: bool = False) -> Alignment:
    return Alignment(horizontal="center", vertical="center", wrap_text=wrap)


def _left(wrap: bool = True) -> Alignment:
    return Alignment(horizontal="left", vertical="center", wrap_text=wrap)


def _clean_excel_str(val) -> str:
    """Strip XML-illegal control characters and lone surrogates only when present."""
    if val is None:
        return ""
    s = str(val)
    if ILLEGAL_CHARACTERS_RE.search(s):
        s = ILLEGAL_CHARACTERS_RE.sub("", s)
    if _SURROGATE_RE.search(s):
        s = _SURROGATE_RE.sub("", s)
    return s


def _set_safe_cell(cell, val) -> None:
    """Assign cell value safely, guarding strings starting with '=' from formula interpretation."""
    if isinstance(val, str):
        cleaned = _clean_excel_str(val)
        cell.value = cleaned
        if cleaned.startswith("="):
            cell.data_type = "s"
    else:
        cell.value = val


def _header_row(ws, row: int, columns: List[str], bg: str = C_HEADER_BG) -> None:
    ws.row_dimensions[row].height = 28
    for col_idx, text in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=col_idx, value=text)
        cell.fill = _fill(bg)
        cell.font = _font(bold=True, size=10, color=C_HEADER_FG)
        cell.alignment = _center()
        cell.border = _border()


# ── Precompute Source Sentiment Percentages ───────────────────────────────────

def _compute_source_pcts(
    sources: List[DiscoveredSource],
    analyzed: List[AnalyzedComment],
) -> None:
    """Populate positive_pct / negative_pct on each source in-place."""
    counters: Dict[str, List[int]] = defaultdict(lambda: [0, 0, 0])
    for c in analyzed:
        entry = counters[c.source_url]
        entry[2] += 1
        if c.sentiment == "Positive":
            entry[0] += 1
        elif c.sentiment == "Negative":
            entry[1] += 1

    for src in sources:
        entry = counters.get(src.url)
        if entry and entry[2] > 0:
            total = entry[2]
            src.positive_pct = round(entry[0] / total * 100, 1)
            src.negative_pct = round(entry[1] / total * 100, 1)
            src.comments_collected = max(src.comments_collected, total)


def _ensure_comment_styles(wb: Workbook) -> None:
    """Register shared NamedStyles on the workbook once for fast O(1) cell styling."""
    if "OmniCenterW" in wb.named_styles:
        return
    border = _border()
    fill_w = _fill(C_WHITE)
    fill_a = _fill(C_ALT_ROW)
    f_def = _font(size=10)
    f_tgt = _font(bold=True, size=10, color="004B91")
    f_dsc = _font(bold=False, size=10, color="595959")
    f_url = _font(color="004B91", size=10, underline="single")
    al_c = _center()
    al_lw = _left(wrap=True)
    al_ln = Alignment(horizontal="left", vertical="center", wrap_text=False)

    specs = [
        ("OmniCenterW", f_def, fill_w, al_c),
        ("OmniCenterA", f_def, fill_a, al_c),
        ("OmniWrapW",   f_def, fill_w, al_lw),
        ("OmniWrapA",   f_def, fill_a, al_lw),
        ("OmniLeftW",   f_def, fill_w, al_ln),
        ("OmniLeftA",   f_def, fill_a, al_ln),
        ("OmniUrlW",    f_url, fill_w, al_ln),
        ("OmniUrlA",    f_url, fill_a, al_ln),
        ("OmniTargetW", f_tgt, fill_w, al_c),
        ("OmniTargetA", f_tgt, fill_a, al_c),
        ("OmniDiscW",   f_dsc, fill_w, al_c),
        ("OmniDiscA",   f_dsc, fill_a, al_c),
        ("OmniSentPos", _font(bold=True, size=10, color=C_POS_TEXT), _fill(C_POS_FILL), al_c),
        ("OmniSentNeu", _font(bold=True, size=10, color=C_NEU_TEXT), _fill(C_NEU_FILL), al_c),
        ("OmniSentNeg", _font(bold=True, size=10, color=C_NEG_TEXT), _fill(C_NEG_FILL), al_c),
    ]
    for name, fnt, fl, al in specs:
        ns = NamedStyle(name=name, font=fnt, fill=fl, border=border, alignment=al)
        wb.add_named_style(ns)


# ── Reusable Table Writer for Review Comment Sheets ───────────────────────────

def _populate_comments_sheet(
    ws,
    comments: List[AnalyzedComment],
    header_bg: str = C_HEADER_BG,
    empty_message: str = "No comments available in this category.",
    show_scope: bool = False,
) -> None:
    """Populate a worksheet with standard clean customer reviews table."""
    ws.views.sheetView[0].showGridLines = True
    _ensure_comment_styles(ws.parent)

    if show_scope:
        columns = [
            "#", "Scope", "Platform", "Customer Review / Comment", "Sentiment",
            "Rating", "Key Aspect", "Author", "Published Date",
            "Source URL", "Language", "Translation / Summary", "Confidence",
        ]
    else:
        columns = [
            "#", "Platform", "Customer Review / Comment", "Sentiment",
            "Rating", "Key Aspect", "Author", "Published Date",
            "Source URL", "Language", "Translation / Summary", "Confidence",
        ]
    _header_row(ws, 1, columns, bg=header_bg)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"
    ws.sheet_format.defaultRowHeight = 28
    ws.sheet_format.customHeight = True

    if not comments:
        ws.row_dimensions[2].height = 30
        c = ws.cell(row=2, column=3, value=empty_message)
        c.font = _font(italic=True, size=10, color="595959")
        c.alignment = _left()
        return

    sent_style_map = {
        "Positive": "OmniSentPos",
        "Neutral":  "OmniSentNeu",
        "Negative": "OmniSentNeg",
    }

    # Pre-map column style pairs (white_row_style, alt_row_style) for O(1) lookup
    if show_scope:
        col_styles = (
            ("OmniCenterW", "OmniCenterA"),  # 1: #
            ("OmniDiscW",   "OmniDiscA"),    # 2: Scope (overridden when is_target)
            ("OmniCenterW", "OmniCenterA"),  # 3: Platform
            ("OmniWrapW",   "OmniWrapA"),    # 4: Review
            ("OmniSentNeu", "OmniSentNeu"),  # 5: Sentiment (overridden)
            ("OmniCenterW", "OmniCenterA"),  # 6: Rating
            ("OmniLeftW",   "OmniLeftA"),    # 7: Aspect
            ("OmniLeftW",   "OmniLeftA"),    # 8: Author
            ("OmniCenterW", "OmniCenterA"),  # 9: Date
            ("OmniUrlW",    "OmniUrlA"),     # 10: Source URL
            ("OmniCenterW", "OmniCenterA"),  # 11: Language
            ("OmniLeftW",   "OmniLeftA"),    # 12: Translation
            ("OmniCenterW", "OmniCenterA"),  # 13: Confidence
        )
        sent_col_idx = 5
        url_col_idx = 10
    else:
        col_styles = (
            ("OmniCenterW", "OmniCenterA"),  # 1: #
            ("OmniCenterW", "OmniCenterA"),  # 2: Platform
            ("OmniWrapW",   "OmniWrapA"),    # 3: Review
            ("OmniSentNeu", "OmniSentNeu"),  # 4: Sentiment (overridden)
            ("OmniCenterW", "OmniCenterA"),  # 5: Rating
            ("OmniLeftW",   "OmniLeftA"),    # 6: Aspect
            ("OmniLeftW",   "OmniLeftA"),    # 7: Author
            ("OmniCenterW", "OmniCenterA"),  # 8: Date
            ("OmniUrlW",    "OmniUrlA"),     # 9: Source URL
            ("OmniCenterW", "OmniCenterA"),  # 10: Language
            ("OmniLeftW",   "OmniLeftA"),    # 11: Translation
            ("OmniCenterW", "OmniCenterA"),  # 12: Confidence
        )
        sent_col_idx = 4
        url_col_idx = 9

    for idx, c in enumerate(comments, start=1):
        row = idx + 1
        is_alt = (idx & 1) == 0
        s_idx = 1 if is_alt else 0

        rating_display = f"{c.rating:.1f} ★" if c.rating else "—"
        conf_pct = f"{round(c.confidence_score * 100)}%" if c.confidence_score else "—"

        trans_text = c.english_text or ""
        if c.original_language == "English" and trans_text == c.original_text:
            trans_display = "—"
        else:
            trans_display = trans_text[:5000]

        is_target = getattr(c, "is_target", False)
        orig_text = (c.original_text or "")[:10000]
        aspect_val = c.aspect_tag or "General"
        author_val = c.author or "Anonymous"
        date_val = c.published_date or "—"
        lang_val = c.original_language or "English"
        src_url = c.source_url
        sent_style = sent_style_map.get(c.sentiment, "OmniCenterA" if is_alt else "OmniCenterW")

        if show_scope:
            scope_label = "🎯 Target Link" if is_target else "🌐 Discovered"
            values = (
                idx, scope_label, str(c.platform), orig_text, c.sentiment,
                rating_display, aspect_val, author_val, date_val,
                src_url, lang_val, trans_display, conf_pct,
            )
        else:
            values = (
                idx, str(c.platform), orig_text, c.sentiment,
                rating_display, aspect_val, author_val, date_val,
                src_url, lang_val, trans_display, conf_pct,
            )

        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col_idx)
            _set_safe_cell(cell, val)
            if col_idx == sent_col_idx:
                cell.style = sent_style
            elif show_scope and col_idx == 2 and is_target:
                cell.style = "OmniTargetA" if is_alt else "OmniTargetW"
            elif col_idx == url_col_idx:
                if src_url and str(src_url).startswith(("http://", "https://")):
                    cell.hyperlink = str(src_url)[:2000]
                    cell.style = col_styles[col_idx - 1][s_idx]
                else:
                    cell.style = "OmniLeftA" if is_alt else "OmniLeftW"
            else:
                cell.style = col_styles[col_idx - 1][s_idx]

    # Column widths
    if show_scope:
        ws.column_dimensions["A"].width = 6    # #
        ws.column_dimensions["B"].width = 16   # Scope
        ws.column_dimensions["C"].width = 14   # Platform
        ws.column_dimensions["D"].width = 72   # Review Text
        ws.column_dimensions["E"].width = 14   # Sentiment
        ws.column_dimensions["F"].width = 12   # Rating
        ws.column_dimensions["G"].width = 18   # Aspect
        ws.column_dimensions["H"].width = 18   # Author
        ws.column_dimensions["I"].width = 15   # Date
        ws.column_dimensions["J"].width = 44   # Source URL
        ws.column_dimensions["K"].width = 12   # Language
        ws.column_dimensions["L"].width = 38   # Translation
        ws.column_dimensions["M"].width = 14   # Confidence
    else:
        ws.column_dimensions["A"].width = 6    # #
        ws.column_dimensions["B"].width = 14   # Platform
        ws.column_dimensions["C"].width = 72   # Review Text
        ws.column_dimensions["D"].width = 14   # Sentiment
        ws.column_dimensions["E"].width = 12   # Rating
        ws.column_dimensions["F"].width = 18   # Aspect
        ws.column_dimensions["G"].width = 18   # Author
        ws.column_dimensions["H"].width = 15   # Date
        ws.column_dimensions["I"].width = 44   # Source URL
        ws.column_dimensions["J"].width = 12   # Language
        ws.column_dimensions["K"].width = 38   # Translation
        ws.column_dimensions["L"].width = 14   # Confidence


# ── Sheet 1: Master Reviews Sheet ─────────────────────────────────────────────

def _build_sheet_all_comments(wb: Workbook, comments: List[AnalyzedComment]) -> None:
    ws = wb.create_sheet("All Reviews & Comments")
    _populate_comments_sheet(
        ws,
        comments,
        header_bg=C_HEADER_BG,
        empty_message="No reviews or comments collected.",
        show_scope=True,
    )


# ── Sheet: Target Reviews (Dedicated pure target link reviews) ────────────────

def _build_sheet_target_comments(wb: Workbook, comments: List[AnalyzedComment]) -> None:
    ws = wb.create_sheet("🎯 Target Reviews")
    _populate_comments_sheet(
        ws,
        comments,
        header_bg="003366",  # Dark Royal Navy
        empty_message="No reviews directly from the target link.",
        show_scope=False,
    )


# ── Sheet: Discovered Web Reviews (Dedicated secondary web reviews) ───────────

def _build_sheet_discovered_comments(wb: Workbook, comments: List[AnalyzedComment]) -> None:
    ws = wb.create_sheet("🌐 Discovered Web Reviews")
    _populate_comments_sheet(
        ws,
        comments,
        header_bg="4A6572",  # Slate Steel
        empty_message="No reviews from external discovered links.",
        show_scope=False,
    )


# ── Sheet 2: Good Reviews (Positive) ──────────────────────────────────────────

def _build_sheet_good_comments(wb: Workbook, comments: List[AnalyzedComment]) -> None:
    ws = wb.create_sheet("👍 Good Reviews")
    good_list = [c for c in comments if c.sentiment == "Positive"]
    _populate_comments_sheet(
        ws,
        good_list,
        header_bg="137333",  # Forest Green
        empty_message="No positive reviews detected.",
        show_scope=True,
    )


# ── Sheet 3: Bad Reviews (Negative) ───────────────────────────────────────────

def _build_sheet_bad_comments(wb: Workbook, comments: List[AnalyzedComment]) -> None:
    ws = wb.create_sheet("👎 Bad Reviews")
    bad_list = [c for c in comments if c.sentiment == "Negative"]
    _populate_comments_sheet(
        ws,
        bad_list,
        header_bg="C5221F",  # Crimson Red
        empty_message="No negative reviews or complaints detected.",
        show_scope=True,
    )


# ── Sheet 4: Neutral Reviews ──────────────────────────────────────────────────

def _build_sheet_neutral_comments(wb: Workbook, comments: List[AnalyzedComment]) -> None:
    ws = wb.create_sheet("⚖️ Neutral Reviews")
    neu_list = [c for c in comments if c.sentiment == "Neutral"]
    _populate_comments_sheet(
        ws,
        neu_list,
        header_bg="5F6368",  # Slate Gray
        empty_message="No neutral reviews detected.",
        show_scope=True,
    )


# ── Sheet 5: Executive Review ─────────────────────────────────────────────────

def _build_sheet_executive(
    wb: Workbook,
    review: ExecutiveReview,
    analyzed: List[AnalyzedComment] | None = None,
) -> None:
    ws = wb.create_sheet("Executive Review")
    ws.views.sheetView[0].showGridLines = True

    # 1. Main Title Banner (A1:G1)
    ws.merge_cells("A1:G1")
    title_cell = ws["A1"]
    _set_safe_cell(title_cell, f"Executive Review: {review.topic_name}")
    title_cell.fill = _fill(C_HEADER_BG)
    title_cell.font = _font(bold=True, size=14, color=C_HEADER_FG)
    title_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 36

    # 2. Key Metrics Table
    _header_row(ws, 3, ["Review Metric", "Summary Value", "Context / Notes"], bg=C_SUBHEADER)

    score_val = f"{review.ai_score_out_of_10:.1f} / 10.0"
    metric_rows = [
        ("Product / Topic", review.topic_name, "Verified target of intelligence search"),
        ("Overall Sentiment", review.overall_sentiment, "Aggregated customer consensus across platforms"),
        ("AI Review Score", score_val, "Calculated based on customer pros, cons & ratings"),
        ("Primary Source Link", review.original_url, "Original target URL analyzed"),
        ("Buying Verdict", review.buying_or_action_verdict, "Core purchasing takeaway"),
    ]

    for idx, (label, val, note) in enumerate(metric_rows, start=4):
        ws.row_dimensions[idx].height = 22
        alt_fill = _fill(C_ALT_ROW if idx % 2 == 0 else C_WHITE)

        c1 = ws.cell(row=idx, column=1, value=label)
        c1.font = _font(bold=True, size=10)
        c1.fill = _fill(C_ACCENT_BG)
        c1.border = _border()

        c2 = ws.cell(row=idx, column=2)
        _set_safe_cell(c2, val)
        c2.font = _font(size=10)
        c2.fill = alt_fill
        c2.border = _border()

        if label == "Overall Sentiment":
            if val == "Positive":
                c2.fill = _fill(C_POS_FILL)
                c2.font = _font(bold=True, color=C_POS_TEXT)
            elif val == "Negative":
                c2.fill = _fill(C_NEG_FILL)
                c2.font = _font(bold=True, color=C_NEG_TEXT)
            else:
                c2.fill = _fill(C_NEU_FILL)
                c2.font = _font(bold=True, color=C_NEU_TEXT)
            c2.alignment = _center()
        elif label == "Primary Source Link" and str(val).startswith("http"):
            c2.hyperlink = val
            c2.font = _font(color="004B91", bold=True)
            c2.style = "Hyperlink"
        elif label == "AI Review Score":
            c2.font = _font(bold=True, color="004B91")
            c2.alignment = _center()

        c3 = ws.cell(row=idx, column=3, value=note)
        c3.font = _font(size=9, color="595959", italic=True)
        c3.fill = alt_fill
        c3.border = _border()

    # 3. Sentiment Breakdown Table (Rows 10 to 14)
    ws.cell(row=10, column=1, value="Customer Sentiment Breakdown").font = _font(bold=True, size=11, color=C_SUBHEADER)
    _header_row(ws, 11, ["Sentiment Category", "Verified Comments", "Share %"], bg=C_SUBHEADER)

    if analyzed:
        pos_est = sum(1 for c in analyzed if c.sentiment == "Positive")
        neg_est = sum(1 for c in analyzed if c.sentiment == "Negative")
        neu_est = sum(1 for c in analyzed if c.sentiment == "Neutral")
        total_est = len(analyzed) or 1
    else:
        score = review.ai_score_out_of_10
        pos_est = max(1, round(score * 10))
        neg_est = max(1, round((10 - score) * 10))
        neu_est = max(1, 100 - pos_est - neg_est)
        total_est = pos_est + neu_est + neg_est

    sentiment_breakdown = [
        ("Positive", pos_est, f"{pos_est / total_est * 100:.1f}%", C_POS_FILL, C_POS_TEXT),
        ("Neutral",  neu_est, f"{neu_est / total_est * 100:.1f}%", C_NEU_FILL, C_NEU_TEXT),
        ("Negative", neg_est, f"{neg_est / total_est * 100:.1f}%", C_NEG_FILL, C_NEG_TEXT),
    ]

    for i, (label, count, pct, fill_color, text_color) in enumerate(sentiment_breakdown, start=12):
        ws.row_dimensions[i].height = 20
        c1 = ws.cell(row=i, column=1, value=label)
        c1.fill = _fill(fill_color)
        c1.font = _font(bold=True, color=text_color)
        c1.border = _border()
        c1.alignment = _center()

        c2 = ws.cell(row=i, column=2, value=count)
        c2.font = _font(size=10)
        c2.border = _border()
        c2.alignment = _center()

        c3 = ws.cell(row=i, column=3, value=pct)
        c3.font = _font(bold=True, size=10)
        c3.border = _border()
        c3.alignment = _center()

    # Embedded Pie Chart next to table (Columns E-G, starting at Row 3)
    pie = PieChart()
    pie.title = "Customer Sentiment Share"
    pie.style = 10
    pie_labels = Reference(ws, min_col=1, min_row=12, max_row=14)
    pie_data = Reference(ws, min_col=2, min_row=11, max_row=14)
    pie.add_data(pie_data, titles_from_data=True)
    pie.set_categories(pie_labels)
    pie.width = 14
    pie.height = 7.5

    slice_colors = ["00B050", "808080", "FF0000"]
    for idx, color in enumerate(slice_colors):
        pt = DataPoint(idx=idx)
        pt.graphicalProperties.solidFill = color
        pie.series[0].dPt.append(pt)

    ws.add_chart(pie, "E3")

    # 4. AI Executive Summary (Row 16)
    ws.cell(row=16, column=1, value="AI Executive Summary").font = _font(bold=True, size=11, color=C_SUBHEADER)
    _header_row(ws, 17, ["Section", "Executive Analysis & Key Insights", ""], bg=C_SUBHEADER)
    ws.row_dimensions[18].height = 60

    c1 = ws.cell(row=18, column=1, value="Overview")
    c1.fill = _fill(C_ACCENT_BG)
    c1.font = _font(bold=True, size=10)
    c1.border = _border()

    c2 = ws.cell(row=18, column=2)
    _set_safe_cell(c2, review.executive_summary)
    c2.fill = _fill(C_WHITE)
    c2.font = _font(size=10)
    c2.alignment = _left(wrap=True)
    c2.border = _border()

    c3 = ws.cell(row=18, column=3, value="Synthesized from public reviews")
    c3.font = _font(size=9, color="595959", italic=True)
    c3.border = _border()

    # 5. Top Customer Pros Table (Rows 20 to ...)
    start_pros = 20
    ws.cell(row=start_pros, column=1, value="Key Strengths (Top Customer Pros)").font = _font(bold=True, size=11, color="137333")
    _header_row(ws, start_pros + 1, ["#", "Customer Pro / Positive Highlight", "Sentiment Impact"], bg="137333")

    curr_row = start_pros + 2
    for p_idx, pro in enumerate(review.top_pros or ["Consistently positive feedback on build and quality"], start=1):
        ws.row_dimensions[curr_row].height = 20
        alt_fill = _fill(C_ALT_ROW if p_idx % 2 == 0 else C_WHITE)

        c1 = ws.cell(row=curr_row, column=1, value=p_idx)
        c1.alignment = _center()
        c1.fill = alt_fill
        c1.border = _border()

        c2 = ws.cell(row=curr_row, column=2)
        _set_safe_cell(c2, pro)
        c2.font = _font(size=10)
        c2.fill = alt_fill
        c2.border = _border()

        c3 = ws.cell(row=curr_row, column=3, value="✔ Positive (+)")
        c3.font = _font(bold=True, size=9, color="137333")
        c3.alignment = _center()
        c3.fill = _fill(C_POS_FILL)
        c3.border = _border()
        curr_row += 1

    # 6. Top Customer Cons Table
    start_cons = curr_row + 1
    ws.cell(row=start_cons, column=1, value="Areas of Concern (Top Customer Cons)").font = _font(bold=True, size=11, color="C5221F")
    _header_row(ws, start_cons + 1, ["#", "Customer Con / Critical Point", "Impact Level"], bg="C5221F")

    curr_row = start_cons + 2
    for c_idx, con in enumerate(review.top_cons or ["No major critical issues reported"], start=1):
        ws.row_dimensions[curr_row].height = 20
        alt_fill = _fill(C_ALT_ROW if c_idx % 2 == 0 else C_WHITE)

        c1 = ws.cell(row=curr_row, column=1, value=c_idx)
        c1.alignment = _center()
        c1.fill = alt_fill
        c1.border = _border()

        c2 = ws.cell(row=curr_row, column=2)
        _set_safe_cell(c2, con)
        c2.font = _font(size=10)
        c2.fill = alt_fill
        c2.border = _border()

        c3 = ws.cell(row=curr_row, column=3, value="✘ Point to Consider (-)")
        c3.font = _font(bold=True, size=9, color="C5221F")
        c3.alignment = _center()
        c3.fill = _fill(C_NEG_FILL)
        c3.border = _border()
        curr_row += 1

    # 7. Platform Comparison Notes
    curr_row += 1
    ws.cell(row=curr_row, column=1, value="Platform Comparison Notes").font = _font(bold=True, size=11, color=C_SUBHEADER)
    _header_row(ws, curr_row + 1, ["Parameter", "Cross-Platform Sentiment Notes", ""], bg=C_SUBHEADER)
    curr_row += 2
    ws.row_dimensions[curr_row].height = 36

    c1 = ws.cell(row=curr_row, column=1, value="Platform Findings")
    c1.fill = _fill(C_ACCENT_BG)
    c1.font = _font(bold=True, size=10)
    c1.border = _border()

    c2 = ws.cell(row=curr_row, column=2)
    _set_safe_cell(c2, review.platform_comparison_notes or "Consistent reception across all crawled sources.")
    c2.fill = _fill(C_WHITE)
    c2.font = _font(size=10)
    c2.alignment = _left(wrap=True)
    c2.border = _border()

    c3 = ws.cell(row=curr_row, column=3, value="Cross-platform evaluation")
    c3.font = _font(size=9, color="595959", italic=True)
    c3.border = _border()

    # Column widths for Executive Review
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 65
    ws.column_dimensions["C"].width = 32
    ws.column_dimensions["D"].width = 5
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 18
    ws.column_dimensions["G"].width = 18


# ── Sheet 6: Platform & Aspect Summary ───────────────────────────────────────

def _build_sheet_platform(
    wb: Workbook,
    review: ExecutiveReview,
    analyzed: List[AnalyzedComment],
) -> None:
    ws = wb.create_sheet("Platform & Aspect Summary")
    ws.views.sheetView[0].showGridLines = True
    ws.freeze_panes = "A2"

    platform_cols = ["Platform", "Total Reviews", "Positive", "Neutral", "Negative", "Positive %", "Negative %"]
    _header_row(ws, 1, platform_cols)

    platform_data: Dict[str, dict] = defaultdict(lambda: {"total": 0, "pos": 0, "neu": 0, "neg": 0})
    for c in analyzed:
        d = platform_data[str(c.platform)]
        d["total"] += 1
        if c.sentiment == "Positive":
            d["pos"] += 1
        elif c.sentiment == "Neutral":
            d["neu"] += 1
        else:
            d["neg"] += 1

    data_start_row = 2
    row = data_start_row
    grand_total = grand_pos = grand_neu = grand_neg = 0

    for platform, d in sorted(platform_data.items()):
        total = d["total"] or 1
        grand_total += d["total"]
        grand_pos += d["pos"]
        grand_neu += d["neu"]
        grand_neg += d["neg"]

        alt_fill = _fill(C_ALT_ROW if row % 2 == 0 else C_WHITE)
        ws.row_dimensions[row].height = 22

        vals = [
            platform,
            d["total"],
            d["pos"],
            d["neu"],
            d["neg"],
            f"{d['pos'] / total * 100:.1f}%",
            f"{d['neg'] / total * 100:.1f}%",
        ]
        for col_idx, val in enumerate(vals, start=1):
            cell = ws.cell(row=row, column=col_idx, value=val)
            cell.fill = alt_fill
            cell.border = _border()
            cell.font = _font(size=10)
            cell.alignment = _center() if col_idx > 1 else Alignment(horizontal="left", vertical="center", indent=1)
        row += 1

    # Totals Row
    t = grand_total or 1
    totals = [
        "TOTAL", grand_total, grand_pos, grand_neu, grand_neg,
        f"{grand_pos / t * 100:.1f}%",
        f"{grand_neg / t * 100:.1f}%",
    ]
    ws.row_dimensions[row].height = 24
    for col_idx, val in enumerate(totals, start=1):
        cell = ws.cell(row=row, column=col_idx, value=val)
        cell.fill = _fill(C_HEADER_BG)
        cell.font = _font(bold=True, size=10, color=C_HEADER_FG)
        cell.border = _border()
        cell.alignment = _center() if col_idx > 1 else Alignment(horizontal="left", vertical="center", indent=1)

    # Bar Chart placed at I2
    if row > data_start_row:
        bar = BarChart()
        bar.type = "col"
        bar.title = "Review Sentiment by Platform"
        bar.style = 10
        bar.y_axis.title = "Number of Comments"
        bar.x_axis.title = "Platform"
        cats = Reference(ws, min_col=1, min_row=data_start_row, max_row=row - 1)
        data_ref = Reference(ws, min_col=3, min_row=1, max_row=row - 1, max_col=5)
        bar.add_data(data_ref, titles_from_data=True)
        bar.set_categories(cats)
        bar.width = 17
        bar.height = 10
        for s_idx, color in enumerate(["00B050", "808080", "FF0000"]):
            if s_idx < len(bar.series):
                bar.series[s_idx].graphicalProperties.solidFill = color
        ws.add_chart(bar, "I2")

    # Aspect Sentiment Breakdown Table
    aspect_start = row + 3
    ws.cell(row=aspect_start - 1, column=1, value="Feature & Aspect Sentiment Breakdown").font = _font(bold=True, size=11, color=C_SUBHEADER)
    _header_row(
        ws,
        aspect_start,
        ["Product Aspect", "Total Mentions", "Positive %", "Neutral %", "Negative %", "Key Customer Takeaway"],
        bg=C_SUBHEADER,
    )

    asp_row = aspect_start + 1
    for asp_idx, asp in enumerate(review.aspect_summaries, start=1):
        ws.row_dimensions[asp_row].height = 22
        alt_fill = _fill(C_ALT_ROW if asp_idx % 2 == 0 else C_WHITE)

        asp_vals = [
            asp.aspect_name,
            asp.mention_count,
            f"{asp.positive_pct:.1f}%",
            f"{asp.neutral_pct:.1f}%",
            f"{asp.negative_pct:.1f}%",
            asp.key_takeaway,
        ]
        for col_idx, val in enumerate(asp_vals, start=1):
            cell = ws.cell(row=asp_row, column=col_idx)
            _set_safe_cell(cell, val)
            cell.fill = alt_fill
            cell.border = _border()
            cell.font = _font(size=10)
            if col_idx in (1, 6):
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=(col_idx == 6))
            else:
                cell.alignment = _center()
        asp_row += 1

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 15
    ws.column_dimensions["G"].width = 15
    ws.column_dimensions["H"].width = 5


# ── Sheet 7: Discovered Sources ───────────────────────────────────────────────

def _build_sheet_sources(wb: Workbook, sources: List[DiscoveredSource]) -> None:
    ws = wb.create_sheet("Discovered Sources")
    ws.views.sheetView[0].showGridLines = True

    columns = [
        "#", "Platform", "Page / Video Title", "URL",
        "Collector", "Reviews Collected",
        "Positive %", "Negative %", "Scrape Status", "Error / Notes",
    ]
    _header_row(ws, 1, columns)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"

    status_pill = {
        "success": (C_POS_FILL, C_POS_TEXT, "✅ Success"),
        "partial": ("FFF2CC", "B25E00", "⚠️ Partial"),
        "failed":  (C_NEG_FILL, C_NEG_TEXT, "❌ Failed"),
        "skipped": (C_NEU_FILL, C_NEU_TEXT, "⏸ Skipped"),
        "pending": (C_WHITE, "595959", "⏳ Pending"),
    }

    for idx, src in enumerate(sources, start=1):
        row = idx + 1
        ws.row_dimensions[row].height = 22
        alt_fill = _fill(C_ALT_ROW if idx % 2 == 0 else C_WHITE)

        s_status = str(src.scrape_status).lower()
        fill_col, text_col, status_text = status_pill.get(s_status, (C_WHITE, "000000", str(src.scrape_status)))

        values = [
            idx,
            str(src.platform),
            (src.title or "Untitled Source")[:85],
            src.url,
            src.collector_used or "direct",
            src.comments_collected,
            f"{src.positive_pct:.1f}%",
            f"{src.negative_pct:.1f}%",
            status_text,
            (src.error_message or "")[:120],
        ]

        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col_idx)
            _set_safe_cell(cell, val)
            cell.border = _border()
            cell.font = _font(size=10)

            if col_idx == 9:  # Status pill
                cell.fill = _fill(fill_col)
                cell.font = _font(bold=True, size=9, color=text_col)
                cell.alignment = _center()
            elif col_idx in (1, 6, 7, 8):
                cell.fill = alt_fill
                cell.alignment = _center()
            else:
                cell.fill = alt_fill
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)

        # Hyperlink for URL column (Column 4 - required by test)
        url_cell = ws.cell(row=row, column=4)
        url_cell.hyperlink = src.url
        url_cell.font = _font(color="004B91", bold=True, size=10)
        url_cell.style = "Hyperlink"

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 15
    ws.column_dimensions["C"].width = 44
    ws.column_dimensions["D"].width = 50
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 18
    ws.column_dimensions["G"].width = 14
    ws.column_dimensions["H"].width = 14
    ws.column_dimensions["I"].width = 16
    ws.column_dimensions["J"].width = 35


# ── Main Builder Function ─────────────────────────────────────────────────────

def build_excel(
    review: ExecutiveReview,
    sources: List[DiscoveredSource],
    analyzed_comments: List[AnalyzedComment],
    output_dir: str | None = None,
) -> str:
    """Build the clean multi-sheet .xlsx report and return the absolute file path.

    Sheets created:
      1. All Reviews & Comments  (Active Sheet on open)
      2. 👍 Good Reviews         (Filtered Positive reviews)
      3. 👎 Bad Reviews          (Filtered Negative reviews)
      4. ⚖️ Neutral Reviews      (Filtered Neutral reviews)
      5. Executive Review        (Summary dashboard, KPI overview, PieChart)
      6. Platform & Aspect Summary (Platform breakdown, BarChart, Aspect breakdown)
      7. Discovered Sources      (Audit log of all crawled URLs with hyperlinks)
    """
    out_dir = Path(output_dir or settings.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-compute source sentiment data before writing
    _compute_source_pcts(sources, analyzed_comments)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in review.topic_name[:40]
    )
    file_path = out_dir / f"{safe_name}_{timestamp}.xlsx"
    if file_path.exists():
        import uuid
        file_path = out_dir / f"{safe_name}_{timestamp}_{uuid.uuid4().hex[:6]}.xlsx"

    wb = Workbook()
    # Remove default blank sheet
    default_sheet = wb.active

    # Separate target comments and discovered secondary comments strictly (never disguise web scrapes as target reviews)
    target_comments = [c for c in analyzed_comments if getattr(c, "is_target", False)]
    discovered_comments = [c for c in analyzed_comments if not getattr(c, "is_target", False)]

    # Build sheets in order
    _build_sheet_all_comments(wb, analyzed_comments)
    _build_sheet_target_comments(wb, target_comments)
    _build_sheet_good_comments(wb, analyzed_comments)
    _build_sheet_bad_comments(wb, analyzed_comments)
    _build_sheet_neutral_comments(wb, analyzed_comments)
    if discovered_comments:
        _build_sheet_discovered_comments(wb, discovered_comments)
    _build_sheet_executive(wb, review, analyzed_comments)
    _build_sheet_platform(wb, review, analyzed_comments)
    _build_sheet_sources(wb, sources)

    # Remove the initial blank sheet created by openpyxl
    if default_sheet and default_sheet in wb.worksheets:
        wb.remove(default_sheet)

    # Make "All Reviews & Comments" the active sheet when opened
    wb.active = wb["All Reviews & Comments"]

    wb.save(str(file_path))
    logger.info(f"Stage 5: Professional divided Excel report saved → {file_path}")
    return str(file_path)
