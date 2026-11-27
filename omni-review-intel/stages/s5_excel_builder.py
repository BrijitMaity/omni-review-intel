"""
stages/s5_excel_builder.py — Stage 5: Streaming write-only openpyxl Excel builder.

Generates a multi-sheet .xlsx workbook in constant (< 20 MB) memory even for
300,000+ rows using openpyxl Workbook(write_only=True) and StyleArray reuse:
  1. All Reviews & Comments     — Master sheet of all analyzed comments (Active Tab)
  2. 🎯 Target Reviews          — Reviews collected directly from the target URL
  3. 👍 Good Reviews            — Positive comments and praise
  4. 👎 Bad Reviews             — Negative comments and complaints
  5. ⚖️ Neutral Reviews         — Neutral comments and questions
  6. 🌐 Discovered Web Reviews  — External web review comments (when present)
  7. Executive Review           — Overview metrics, pros/cons, verdict, embedded PieChart
  8. Platform & Aspect Summary  — Platform/aspect tables + embedded BarChart
  9. Discovered Sources         — Audit log of crawled URLs with hyperlinks
"""

from __future__ import annotations

from collections import defaultdict
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
import re

from loguru import logger
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import settings
from core.schemas import AnalyzedComment, DiscoveredSource, ExecutiveReview

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# ── Professional Color Palette (Corporate / Financial Standard) ───────────────
C_HEADER_BG = "1F4E79"   # Executive Deep Navy
C_HEADER_FG = "FFFFFF"   # Crisp White text
C_SUBHEADER = "2F5597"   # Steel Blue for section headers
C_ACCENT_BG = "D9E1F2"   # Soft Blue Accent
C_BORDER    = "D9D9D9"   # Light Gray clean border
C_ALT_ROW   = "F9FAFB"   # Very subtle zebra row tint
C_WHITE     = "FFFFFF"

# Soft, professional sentiment pill fills & text colors
C_POS_FILL  = "E6F4EA"   # Soft sage green
C_POS_TEXT  = "137333"   # Deep forest green
C_NEU_FILL  = "F1F3F4"   # Soft neutral gray
C_NEU_TEXT  = "5F6368"   # Slate gray
C_NEG_FILL  = "FCE8E6"   # Soft rose pink
C_NEG_TEXT  = "C5221F"   # Dark crimson


# ── Shared Style Primitives ───────────────────────────────────────────────────

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


_BAD_EXCEL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")


def _clean_excel_str(val) -> str:
    """Strip XML-illegal control characters and lone surrogates in one pass."""
    if val is None:
        return ""
    s = val if isinstance(val, str) else str(val)
    if _BAD_EXCEL_RE.search(s):
        s = _BAD_EXCEL_RE.sub("", s)
    return s


def _wo_cell(ws, val=None, *, font=None, fill=None, border=None, alignment=None, style_arr=None, hyperlink: str | None = None, safe: bool = False) -> WriteOnlyCell:
    """Create a WriteOnlyCell with safe formula escaping and optional fast StyleArray."""
    cell = WriteOnlyCell(ws)
    if isinstance(val, str):
        cleaned = val if safe else _clean_excel_str(val)
        cell.value = cleaned
        if cleaned and cleaned[0] == "=":
            cell.data_type = "s"
    else:
        cell.value = val

    if style_arr is not None:
        cell._style = style_arr
    else:
        if font is not None:
            cell.font = font
        if fill is not None:
            cell.fill = fill
        if border is not None:
            cell.border = border
        if alignment is not None:
            cell.alignment = alignment

    if hyperlink:
        cell.hyperlink = hyperlink
    return cell


def _register_comment_style_arrays(wb: Workbook) -> dict[str, object]:
    """Register NamedStyles once on the workbook and extract fast StyleArray copies."""
    if hasattr(wb, "_omni_style_arrays"):
        return wb._omni_style_arrays

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
    probe_ws = wb.create_sheet("__style_probe__")
    arrays: dict[str, object] = {}
    for name, fnt, fl, al in specs:
        if name not in wb.named_styles:
            wb.add_named_style(NamedStyle(name=name, font=fnt, fill=fl, border=border, alignment=al))
        c = WriteOnlyCell(probe_ws, value="")
        c.style = name
        arrays[name] = copy(c._style)
    wb.remove(probe_ws)
    wb._omni_style_arrays = arrays
    return arrays


def _header_cells(ws, columns: List[str], bg: str = C_HEADER_BG) -> list[WriteOnlyCell]:
    fl = _fill(bg)
    fnt = _font(bold=True, size=10, color=C_HEADER_FG)
    al = _center()
    bdr = _border()
    return [_wo_cell(ws, text, font=fnt, fill=fl, border=bdr, alignment=al) for text in columns]


# ── Precompute Source Sentiment Percentages ───────────────────────────────────

def _compute_source_pcts(
    sources: List[DiscoveredSource],
    analyzed: List[AnalyzedComment],
    source_stats: Optional[dict[str, list[int]]] = None,
) -> None:
    """Populate positive_pct / negative_pct on each source in-place."""
    if source_stats is None:
        counters: Dict[str, List[int]] = defaultdict(lambda: [0, 0, 0])
        for c in analyzed:
            entry = counters[c.source_url]
            entry[2] += 1
            if c.sentiment == "Positive":
                entry[0] += 1
            elif c.sentiment == "Negative":
                entry[1] += 1
    else:
        counters = source_stats

    for src in sources:
        entry = counters.get(src.url)
        if entry and entry[2] > 0:
            total = entry[2]
            src.positive_pct = round(entry[0] / total * 100, 1)
            src.negative_pct = round(entry[1] / total * 100, 1)
            src.comments_collected = max(src.comments_collected, total)


# ── Streaming Comment Sheet Writer ────────────────────────────────────────────

def _populate_comments_sheet(
    wb: Workbook,
    title: str,
    comments_iter: Iterable[AnalyzedComment],
    header_bg: str = C_HEADER_BG,
    empty_message: str = "No comments available in this category.",
    show_scope: bool = False,
    progress_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """Create and stream-write a formatted review table sheet in constant RAM."""
    style_arrays = _register_comment_style_arrays(wb)
    ws = wb.create_sheet(title)
    ws.views.sheetView[0].showGridLines = True

    if show_scope:
        columns = [
            "#", "Scope", "Platform", "Customer Review / Comment", "Sentiment",
            "Rating", "Key Aspect", "Author", "Published Date",
            "Source URL", "Language", "Translation / Summary", "Confidence",
        ]
        widths = (
            ("A", 6), ("B", 16), ("C", 14), ("D", 72), ("E", 14), ("F", 12),
            ("G", 18), ("H", 18), ("I", 15), ("J", 44), ("K", 12), ("L", 38), ("M", 14),
        )
    else:
        columns = [
            "#", "Platform", "Customer Review / Comment", "Sentiment",
            "Rating", "Key Aspect", "Author", "Published Date",
            "Source URL", "Language", "Translation / Summary", "Confidence",
        ]
        widths = (
            ("A", 6), ("B", 14), ("C", 72), ("D", 14), ("E", 12), ("F", 18),
            ("G", 18), ("H", 15), ("I", 44), ("J", 12), ("K", 38), ("L", 14),
        )

    for col_letter, width in widths:
        ws.column_dimensions[col_letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"
    ws.sheet_format.defaultRowHeight = 28
    ws.sheet_format.customHeight = True

    ws.append(_header_cells(ws, columns, bg=header_bg))

    sent_style_map = {
        "Positive": style_arrays["OmniSentPos"],
        "Neutral":  style_arrays["OmniSentNeu"],
        "Negative": style_arrays["OmniSentNeg"],
    }

    cw, ca = style_arrays["OmniCenterW"], style_arrays["OmniCenterA"]
    ww, wa = style_arrays["OmniWrapW"], style_arrays["OmniWrapA"]
    lw, la = style_arrays["OmniLeftW"], style_arrays["OmniLeftA"]
    uw, ua = style_arrays["OmniUrlW"], style_arrays["OmniUrlA"]
    tw, ta = style_arrays["OmniTargetW"], style_arrays["OmniTargetA"]
    dw, da = style_arrays["OmniDiscW"], style_arrays["OmniDiscA"]

    wrote_any = False
    for idx, c in enumerate(comments_iter, start=1):
        wrote_any = True
        is_alt = (idx & 1) == 0

        sc_center = ca if is_alt else cw
        sc_wrap = wa if is_alt else ww
        sc_left = la if is_alt else lw
        sc_url = ua if is_alt else uw
        sc_tgt = ta if is_alt else tw
        sc_disc = da if is_alt else dw
        sent_arr = sent_style_map.get(c.sentiment, sc_center)

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
        src_url = c.source_url or ""
        # Cap formal OOXML hyperlink relationships per sheet below Excel's 65,530 limit to prevent corruption & RAM bloat
        url_hl = str(src_url)[:2000] if (idx <= 15_000 and str(src_url).startswith(("http://", "https://"))) else None
        url_arr = sc_url if str(src_url).startswith(("http://", "https://")) else sc_left

        if show_scope:
            scope_label = "🎯 Target Link" if is_target else "🌐 Discovered"
            row = [
                _wo_cell(ws, idx, style_arr=sc_center, safe=True),
                _wo_cell(ws, scope_label, style_arr=(sc_tgt if is_target else sc_disc), safe=True),
                _wo_cell(ws, str(c.platform), style_arr=sc_center, safe=True),
                _wo_cell(ws, orig_text, style_arr=sc_wrap),
                _wo_cell(ws, c.sentiment, style_arr=sent_arr, safe=True),
                _wo_cell(ws, rating_display, style_arr=sc_center, safe=True),
                _wo_cell(ws, aspect_val, style_arr=sc_left, safe=True),
                _wo_cell(ws, author_val, style_arr=sc_left),
                _wo_cell(ws, date_val, style_arr=sc_center, safe=True),
                _wo_cell(ws, src_url, style_arr=url_arr, hyperlink=url_hl),
                _wo_cell(ws, lang_val, style_arr=sc_center, safe=True),
                _wo_cell(ws, trans_display, style_arr=sc_left),
                _wo_cell(ws, conf_pct, style_arr=sc_center, safe=True),
            ]
        else:
            row = [
                _wo_cell(ws, idx, style_arr=sc_center, safe=True),
                _wo_cell(ws, str(c.platform), style_arr=sc_center, safe=True),
                _wo_cell(ws, orig_text, style_arr=sc_wrap),
                _wo_cell(ws, c.sentiment, style_arr=sent_arr, safe=True),
                _wo_cell(ws, rating_display, style_arr=sc_center, safe=True),
                _wo_cell(ws, aspect_val, style_arr=sc_left, safe=True),
                _wo_cell(ws, author_val, style_arr=sc_left),
                _wo_cell(ws, date_val, style_arr=sc_center, safe=True),
                _wo_cell(ws, src_url, style_arr=url_arr, hyperlink=url_hl),
                _wo_cell(ws, lang_val, style_arr=sc_center, safe=True),
                _wo_cell(ws, trans_display, style_arr=sc_left),
                _wo_cell(ws, conf_pct, style_arr=sc_center, safe=True),
            ]
        ws.append(row)

        if progress_fn and idx % 25_000 == 0:
            progress_fn(f"Writing Excel '{title}': {idx:,} rows...")

    if not wrote_any:
        ws.append([
            None,
            None,
            _wo_cell(ws, empty_message, font=_font(italic=True, size=10, color="595959"), alignment=_left()),
        ])


# ── Sheet 5: Executive Review (WriteOnly) ─────────────────────────────────────

def _build_sheet_executive(
    wb: Workbook,
    review: ExecutiveReview,
    analyzed: List[AnalyzedComment] | None = None,
    exact_stats: Optional[dict] = None,
) -> None:
    ws = wb.create_sheet("Executive Review")
    ws.views.sheetView[0].showGridLines = True
    for col_letter, width in (
        ("A", 24), ("B", 65), ("C", 32), ("D", 5), ("E", 18), ("F", 18), ("G", 18)
    ):
        ws.column_dimensions[col_letter].width = width
    ws.merged_cells.add("A1:G1")

    bdr = _border()
    fill_acc = _fill(C_ACCENT_BG)
    fill_hdr = _fill(C_HEADER_BG)
    fill_white = _fill(C_WHITE)

    # Row 1: Main Title Banner (A1:G1)
    ws.append([
        _wo_cell(
            ws,
            f"Executive Review: {review.topic_name}",
            font=_font(bold=True, size=14, color=C_HEADER_FG),
            fill=fill_hdr,
            alignment=Alignment(horizontal="left", vertical="center", indent=1),
        )
    ])
    # Row 2: Spacer
    ws.append([])
    # Row 3: Key Metrics Header
    ws.append(_header_cells(ws, ["Review Metric", "Summary Value", "Context / Notes"], bg=C_SUBHEADER))

    score_val = f"{review.ai_score_out_of_10:.1f} / 10.0"
    metric_rows = [
        ("Product / Topic", review.topic_name, "Verified target of intelligence search"),
        ("Overall Sentiment", review.overall_sentiment, "Aggregated customer consensus across platforms"),
        ("AI Review Score", score_val, "Calculated based on customer pros, cons & ratings"),
        ("Primary Source Link", review.original_url, "Original target URL analyzed"),
        ("Buying Verdict", review.buying_or_action_verdict, "Core purchasing takeaway"),
    ]
    for idx, (label, val, note) in enumerate(metric_rows, start=4):
        alt_fill = _fill(C_ALT_ROW if idx % 2 == 0 else C_WHITE)
        c1 = _wo_cell(ws, label, font=_font(bold=True, size=10), fill=fill_acc, border=bdr)
        if label == "Overall Sentiment":
            if val == "Positive":
                c2_fill, c2_fnt = _fill(C_POS_FILL), _font(bold=True, color=C_POS_TEXT)
            elif val == "Negative":
                c2_fill, c2_fnt = _fill(C_NEG_FILL), _font(bold=True, color=C_NEG_TEXT)
            else:
                c2_fill, c2_fnt = _fill(C_NEU_FILL), _font(bold=True, color=C_NEU_TEXT)
            c2 = _wo_cell(ws, val, font=c2_fnt, fill=c2_fill, border=bdr, alignment=_center())
        elif label == "Primary Source Link" and str(val).startswith("http"):
            c2 = _wo_cell(ws, val, font=_font(color="004B91", bold=True, underline="single"), fill=alt_fill, border=bdr, hyperlink=str(val)[:2000])
        elif label == "AI Review Score":
            c2 = _wo_cell(ws, val, font=_font(bold=True, color="004B91"), fill=alt_fill, border=bdr, alignment=_center())
        else:
            c2 = _wo_cell(ws, val, font=_font(size=10), fill=alt_fill, border=bdr)
        c3 = _wo_cell(ws, note, font=_font(size=9, color="595959", italic=True), fill=alt_fill, border=bdr)
        ws.append([c1, c2, c3])

    # Row 9: Spacer
    ws.append([])
    # Row 10: Sentiment Breakdown Title
    ws.append([_wo_cell(ws, "Customer Sentiment Breakdown", font=_font(bold=True, size=11, color=C_SUBHEADER))])
    # Row 11: Header
    ws.append(_header_cells(ws, ["Sentiment Category", "Verified Comments", "Share %"], bg=C_SUBHEADER))

    if exact_stats and exact_stats.get("total"):
        pos_est = int(exact_stats["positive"])
        neg_est = int(exact_stats["negative"])
        neu_est = int(exact_stats["neutral"])
        total_est = int(exact_stats["total"]) or 1
    elif analyzed:
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

    # Rows 12..14
    for label, count, pct, fill_color, text_color in (
        ("Positive", pos_est, f"{pos_est / total_est * 100:.1f}%", C_POS_FILL, C_POS_TEXT),
        ("Neutral",  neu_est, f"{neu_est / total_est * 100:.1f}%", C_NEU_FILL, C_NEU_TEXT),
        ("Negative", neg_est, f"{neg_est / total_est * 100:.1f}%", C_NEG_FILL, C_NEG_TEXT),
    ):
        ws.append([
            _wo_cell(ws, label, fill=_fill(fill_color), font=_font(bold=True, color=text_color), border=bdr, alignment=_center()),
            _wo_cell(ws, count, font=_font(size=10), border=bdr, alignment=_center()),
            _wo_cell(ws, pct, font=_font(bold=True, size=10), border=bdr, alignment=_center()),
        ])

    # Embedded Pie Chart next to table (E3)
    pie = PieChart()
    pie.title = "Customer Sentiment Share"
    pie.style = 10
    pie_labels = Reference(ws, min_col=1, min_row=12, max_row=14)
    pie_data = Reference(ws, min_col=2, min_row=11, max_row=14)
    pie.add_data(pie_data, titles_from_data=True)
    pie.set_categories(pie_labels)
    pie.width = 14
    pie.height = 7.5
    for idx, color in enumerate(("00B050", "808080", "FF0000")):
        pt = DataPoint(idx=idx)
        pt.graphicalProperties.solidFill = color
        pie.series[0].dPt.append(pt)
    ws.add_chart(pie, "E3")

    # Row 15: Spacer, Row 16-18: Executive Summary
    ws.append([])
    ws.append([_wo_cell(ws, "AI Executive Summary", font=_font(bold=True, size=11, color=C_SUBHEADER))])
    ws.append(_header_cells(ws, ["Section", "Executive Analysis & Key Insights", ""], bg=C_SUBHEADER))
    ws.append([
        _wo_cell(ws, "Overview", fill=fill_acc, font=_font(bold=True, size=10), border=bdr),
        _wo_cell(ws, review.executive_summary, fill=fill_white, font=_font(size=10), alignment=_left(wrap=True), border=bdr),
        _wo_cell(ws, "Synthesized from public reviews", font=_font(size=9, color="595959", italic=True), border=bdr),
    ])

    # Top Pros
    ws.append([])
    ws.append([_wo_cell(ws, "Key Strengths (Top Customer Pros)", font=_font(bold=True, size=11, color="137333"))])
    ws.append(_header_cells(ws, ["#", "Customer Pro / Positive Highlight", "Sentiment Impact"], bg="137333"))
    for p_idx, pro in enumerate(review.top_pros or ["Consistently positive feedback on build and quality"], start=1):
        alt_fill = _fill(C_ALT_ROW if p_idx % 2 == 0 else C_WHITE)
        ws.append([
            _wo_cell(ws, p_idx, alignment=_center(), fill=alt_fill, border=bdr),
            _wo_cell(ws, pro, font=_font(size=10), fill=alt_fill, border=bdr),
            _wo_cell(ws, "✔ Positive (+)", font=_font(bold=True, size=9, color="137333"), alignment=_center(), fill=_fill(C_POS_FILL), border=bdr),
        ])

    # Top Cons
    ws.append([])
    ws.append([_wo_cell(ws, "Areas of Concern (Top Customer Cons)", font=_font(bold=True, size=11, color="C5221F"))])
    ws.append(_header_cells(ws, ["#", "Customer Con / Critical Point", "Impact Level"], bg="C5221F"))
    for c_idx, con in enumerate(review.top_cons or ["No major critical issues reported"], start=1):
        alt_fill = _fill(C_ALT_ROW if c_idx % 2 == 0 else C_WHITE)
        ws.append([
            _wo_cell(ws, c_idx, alignment=_center(), fill=alt_fill, border=bdr),
            _wo_cell(ws, con, font=_font(size=10), fill=alt_fill, border=bdr),
            _wo_cell(ws, "✘ Point to Consider (-)", font=_font(bold=True, size=9, color="C5221F"), alignment=_center(), fill=_fill(C_NEG_FILL), border=bdr),
        ])

    # Platform Comparison Notes
    ws.append([])
    ws.append([_wo_cell(ws, "Platform Comparison Notes", font=_font(bold=True, size=11, color=C_SUBHEADER))])
    ws.append(_header_cells(ws, ["Parameter", "Cross-Platform Sentiment Notes", ""], bg=C_SUBHEADER))
    ws.append([
        _wo_cell(ws, "Platform Findings", fill=fill_acc, font=_font(bold=True, size=10), border=bdr),
        _wo_cell(ws, review.platform_comparison_notes or "Consistent reception across all crawled sources.", fill=fill_white, font=_font(size=10), alignment=_left(wrap=True), border=bdr),
        _wo_cell(ws, "Cross-platform evaluation", font=_font(size=9, color="595959", italic=True), border=bdr),
    ])


# ── Sheet 6: Platform & Aspect Summary (WriteOnly) ────────────────────────────

def _build_sheet_platform(
    wb: Workbook,
    review: ExecutiveReview,
    analyzed: List[AnalyzedComment],
    exact_stats: Optional[dict] = None,
) -> None:
    ws = wb.create_sheet("Platform & Aspect Summary")
    ws.views.sheetView[0].showGridLines = True
    ws.freeze_panes = "A2"
    for col_letter, width in (
        ("A", 20), ("B", 16), ("C", 14), ("D", 14), ("E", 14), ("F", 15), ("G", 15), ("H", 5)
    ):
        ws.column_dimensions[col_letter].width = width

    platform_cols = ["Platform", "Total Reviews", "Positive", "Neutral", "Negative", "Positive %", "Negative %"]
    ws.append(_header_cells(ws, platform_cols))

    if exact_stats and exact_stats.get("platform_stats"):
        platform_data = {
            k: {
                "total": int(v["total"]),
                "pos": int(v["positive"]),
                "neu": int(v["neutral"]),
                "neg": int(v["negative"]),
            }
            for k, v in exact_stats["platform_stats"].items()
        }
    else:
        platform_data = defaultdict(lambda: {"total": 0, "pos": 0, "neu": 0, "neg": 0})
        for c in analyzed:
            d = platform_data[str(c.platform)]
            d["total"] += 1
            if c.sentiment == "Positive":
                d["pos"] += 1
            elif c.sentiment == "Neutral":
                d["neu"] += 1
            else:
                d["neg"] += 1

    bdr = _border()
    f_row = _font(size=10)
    al_c = _center()
    al_l = Alignment(horizontal="left", vertical="center", indent=1)

    data_start_row = 2
    row_num = data_start_row
    grand_total = grand_pos = grand_neu = grand_neg = 0

    for platform, d in sorted(platform_data.items()):
        total = d["total"] or 1
        grand_total += d["total"]
        grand_pos += d["pos"]
        grand_neu += d["neu"]
        grand_neg += d["neg"]

        alt_fill = _fill(C_ALT_ROW if row_num % 2 == 0 else C_WHITE)
        vals = (
            platform, d["total"], d["pos"], d["neu"], d["neg"],
            f"{d['pos'] / total * 100:.1f}%", f"{d['neg'] / total * 100:.1f}%",
        )
        ws.append([
            _wo_cell(ws, val, fill=alt_fill, border=bdr, font=f_row, alignment=(al_c if col_idx > 1 else al_l))
            for col_idx, val in enumerate(vals, start=1)
        ])
        row_num += 1

    # Totals Row
    t = grand_total or 1
    fill_tot = _fill(C_HEADER_BG)
    f_tot = _font(bold=True, size=10, color=C_HEADER_FG)
    totals = (
        "TOTAL", grand_total, grand_pos, grand_neu, grand_neg,
        f"{grand_pos / t * 100:.1f}%", f"{grand_neg / t * 100:.1f}%",
    )
    ws.append([
        _wo_cell(ws, val, fill=fill_tot, border=bdr, font=f_tot, alignment=(al_c if col_idx > 1 else al_l))
        for col_idx, val in enumerate(totals, start=1)
    ])

    # Bar Chart placed at I2
    if row_num > data_start_row:
        bar = BarChart()
        bar.type = "col"
        bar.title = "Review Sentiment by Platform"
        bar.style = 10
        bar.y_axis.title = "Number of Comments"
        bar.x_axis.title = "Platform"
        cats = Reference(ws, min_col=1, min_row=data_start_row, max_row=row_num - 1)
        data_ref = Reference(ws, min_col=3, min_row=1, max_row=row_num - 1, max_col=5)
        bar.add_data(data_ref, titles_from_data=True)
        bar.set_categories(cats)
        bar.width = 17
        bar.height = 10
        for s_idx, color in enumerate(("00B050", "808080", "FF0000")):
            if s_idx < len(bar.series):
                bar.series[s_idx].graphicalProperties.solidFill = color
        ws.add_chart(bar, "I2")

    # Aspect Sentiment Breakdown Table
    ws.append([])
    ws.append([_wo_cell(ws, "Feature & Aspect Sentiment Breakdown", font=_font(bold=True, size=11, color=C_SUBHEADER))])
    ws.append(_header_cells(
        ws,
        ["Product Aspect", "Total Mentions", "Positive %", "Neutral %", "Negative %", "Key Customer Takeaway"],
        bg=C_SUBHEADER,
    ))
    for asp_idx, asp in enumerate(review.aspect_summaries, start=1):
        alt_fill = _fill(C_ALT_ROW if asp_idx % 2 == 0 else C_WHITE)
        asp_vals = (
            asp.aspect_name, asp.mention_count,
            f"{asp.positive_pct:.1f}%", f"{asp.neutral_pct:.1f}%", f"{asp.negative_pct:.1f}%",
            asp.key_takeaway,
        )
        ws.append([
            _wo_cell(
                ws, val, fill=alt_fill, border=bdr, font=f_row,
                alignment=(Alignment(horizontal="left", vertical="center", wrap_text=(col_idx == 6)) if col_idx in (1, 6) else al_c),
            )
            for col_idx, val in enumerate(asp_vals, start=1)
        ])


# ── Sheet 7: Discovered Sources (WriteOnly) ───────────────────────────────────

def _build_sheet_sources(wb: Workbook, sources: List[DiscoveredSource]) -> None:
    ws = wb.create_sheet("Discovered Sources")
    ws.views.sheetView[0].showGridLines = True
    columns = [
        "#", "Platform", "Page / Video Title", "URL",
        "Collector", "Reviews Collected",
        "Positive %", "Negative %", "Scrape Status", "Error / Notes",
    ]
    for col_letter, width in (
        ("A", 6), ("B", 15), ("C", 44), ("D", 50), ("E", 16),
        ("F", 18), ("G", 14), ("H", 14), ("I", 16), ("J", 35),
    ):
        ws.column_dimensions[col_letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"
    ws.append(_header_cells(ws, columns))

    status_pill = {
        "success": (C_POS_FILL, C_POS_TEXT, "✅ Success"),
        "partial": ("FFF2CC", "B25E00", "⚠️ Partial"),
        "failed":  (C_NEG_FILL, C_NEG_TEXT, "❌ Failed"),
        "skipped": (C_NEU_FILL, C_NEU_TEXT, "⏸ Skipped"),
        "pending": (C_WHITE, "595959", "⏳ Pending"),
    }
    bdr = _border()
    f_row = _font(size=10)
    f_url = _font(color="004B91", bold=True, size=10, underline="single")
    al_c = _center()
    al_l = Alignment(horizontal="left", vertical="center", wrap_text=False)

    for idx, src in enumerate(sources, start=1):
        alt_fill = _fill(C_ALT_ROW if idx % 2 == 0 else C_WHITE)
        s_status = str(src.scrape_status).lower()
        fill_col, text_col, status_text = status_pill.get(s_status, (C_WHITE, "000000", str(src.scrape_status)))

        values = (
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
        )
        row = []
        for col_idx, val in enumerate(values, start=1):
            if col_idx == 4:
                row.append(_wo_cell(ws, val, border=bdr, font=f_url, fill=alt_fill, alignment=al_l, hyperlink=src.url))
            elif col_idx == 9:
                row.append(_wo_cell(ws, val, border=bdr, font=_font(bold=True, size=9, color=text_col), fill=_fill(fill_col), alignment=al_c))
            elif col_idx in (1, 6, 7, 8):
                row.append(_wo_cell(ws, val, border=bdr, font=f_row, fill=alt_fill, alignment=al_c))
            else:
                row.append(_wo_cell(ws, val, border=bdr, font=f_row, fill=alt_fill, alignment=al_l))
        ws.append(row)


# ── Main Builder Function ─────────────────────────────────────────────────────

def build_excel(
    review: ExecutiveReview,
    sources: List[DiscoveredSource],
    analyzed_comments: List[AnalyzedComment],
    output_dir: Optional[str] = None,
    run_store=None,
    exact_stats: Optional[dict] = None,
    progress_fn: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Build the complete 9-sheet Excel report cleanly via write_only streaming
    and return the absolute path to the saved .xlsx file.
    """
    out_dir = Path(output_dir or settings.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in review.topic_name)
    safe_name = "_".join(safe_name.split())[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = out_dir / f"{safe_name}_Review_{timestamp}.xlsx"
    if file_path.exists():
        import uuid
        file_path = out_dir / f"{safe_name}_Review_{timestamp}_{uuid.uuid4().hex[:6]}.xlsx"

    _compute_source_pcts(
        sources,
        analyzed_comments,
        source_stats=(exact_stats or {}).get("source_stats"),
    )

    use_store = bool(run_store is not None and run_store.analyzed_count() > 0)
    wb = Workbook(write_only=True)
    try:
        if use_store:
            discovered_count = (
                int(exact_stats["discovered_count"])
                if exact_stats and "discovered_count" in exact_stats
                else run_store.analyzed_count(is_target=False)
            )
            _populate_comments_sheet(
                wb, "All Reviews & Comments",
                run_store.iter_analyzed(),
                header_bg=C_HEADER_BG,
                empty_message="No reviews or comments collected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "🎯 Target Reviews",
                run_store.iter_analyzed(is_target=True),
                header_bg="003366",
                empty_message="No reviews directly from the target link.",
                show_scope=False,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "👍 Good Reviews",
                run_store.iter_analyzed(sentiment="Positive"),
                header_bg="137333",
                empty_message="No positive reviews detected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "👎 Bad Reviews",
                run_store.iter_analyzed(sentiment="Negative"),
                header_bg="C5221F",
                empty_message="No negative reviews or complaints detected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "⚖️ Neutral Reviews",
                run_store.iter_analyzed(sentiment="Neutral"),
                header_bg="5F6368",
                empty_message="No neutral reviews detected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            if discovered_count > 0:
                _populate_comments_sheet(
                    wb, "🌐 Discovered Web Reviews",
                    run_store.iter_analyzed(is_target=False),
                    header_bg="4A6572",
                    empty_message="No reviews from external discovered links.",
                    show_scope=False,
                    progress_fn=progress_fn,
                )
        else:
            discovered_comments = [c for c in analyzed_comments if not getattr(c, "is_target", False)]
            _populate_comments_sheet(
                wb, "All Reviews & Comments",
                analyzed_comments,
                header_bg=C_HEADER_BG,
                empty_message="No reviews or comments collected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "🎯 Target Reviews",
                (c for c in analyzed_comments if getattr(c, "is_target", False)),
                header_bg="003366",
                empty_message="No reviews directly from the target link.",
                show_scope=False,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "👍 Good Reviews",
                (c for c in analyzed_comments if c.sentiment == "Positive"),
                header_bg="137333",
                empty_message="No positive reviews detected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "👎 Bad Reviews",
                (c for c in analyzed_comments if c.sentiment == "Negative"),
                header_bg="C5221F",
                empty_message="No negative reviews or complaints detected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            _populate_comments_sheet(
                wb, "⚖️ Neutral Reviews",
                (c for c in analyzed_comments if c.sentiment == "Neutral"),
                header_bg="5F6368",
                empty_message="No neutral reviews detected.",
                show_scope=True,
                progress_fn=progress_fn,
            )
            if discovered_comments:
                _populate_comments_sheet(
                    wb, "🌐 Discovered Web Reviews",
                    discovered_comments,
                    header_bg="4A6572",
                    empty_message="No reviews from external discovered links.",
                    show_scope=False,
                    progress_fn=progress_fn,
                )

        _build_sheet_executive(wb, review, analyzed_comments, exact_stats=exact_stats)
        _build_sheet_platform(wb, review, analyzed_comments, exact_stats=exact_stats)
        _build_sheet_sources(wb, sources)

        if wb.worksheets:
            wb.active = 0
        wb.save(str(file_path))
    finally:
        try:
            wb.close()
        except Exception:
            pass

    logger.info(f"Stage 5: Excel report saved -> {file_path} ({file_path.stat().st_size // 1024} KB)")
    return str(file_path)
