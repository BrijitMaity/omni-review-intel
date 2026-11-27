"""
core/pipeline.py — Async orchestrator connecting Stages 1 → 5.

Runs the full pipeline for a given input URL, emitting progress callbacks
at each stage so both CLI and Streamlit display real-time status updates.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Coroutine, Any, Optional

from loguru import logger

from config import scoped_progress_cb, scoped_run_store, settings
from core.run_store import RunStore
from core.schemas import PipelineState
from stages.s1_input_analyzer import analyze_input
from stages.s2_web_discovery import discover_sources
from stages.s3_collectors.router import collect_all
from stages.s4_ai_analyzer import map_analyze, map_reduce_streamed, reduce_synthesize
from stages.s5_excel_builder import build_excel


ProgressCallback = Callable[[str, str], Coroutine[Any, Any, None]]


async def _noop_progress(stage: str, message: str) -> None:
    """Default no-op progress callback (logs via loguru)."""
    logger.info(f"[{stage}] {message}")


async def run_pipeline(
    input_url: str,
    output_dir: Optional[str] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> PipelineState:
    """
    Execute the full omni-review-intel pipeline end to end.

    Args:
        input_url:   The user-supplied URL to analyze.
        output_dir:  Where to save the .xlsx (default: settings.outputs_dir).
        progress_cb: Async callback(stage_name, message) for live UI updates.

    Returns:
        PipelineState with all data populated and output_xlsx_path set.

    Raises:
        RuntimeError: on critical stage failure.
    """
    input_url = (input_url or "").strip()
    if not input_url:
        raise ValueError("input_url cannot be empty")
    if not input_url.startswith(("http://", "https://")):
        input_url = f"https://{input_url}"

    cb = progress_cb or _noop_progress
    state = PipelineState(input_url=input_url)

    logger.info(f"Pipeline START — run_id={state.run_id}, url={input_url}")

    # ── Stage 1: Input Analysis ───────────────────────────────────────────────
    try:
        await cb("Stage 1/5", "Identifying topic from input URL…")
        topic, extra_urls = await analyze_input(input_url, progress_cb=cb)
        state.topic = topic
        await cb("Stage 1/5", f"Topic identified: {topic.canonical_name}")
    except Exception as e:
        msg = f"Stage 1 failed: {e}"
        logger.error(msg)
        state.stage_errors.append(msg)
        raise RuntimeError(msg) from e

    # ── Stage 2: Web Discovery ────────────────────────────────────────────────
    try:
        await cb("Stage 2/5", "Discovering review sources across the web…")
        sources = await discover_sources(
            topic=state.topic,
            input_url=input_url,
            extra_urls=extra_urls,
            progress_cb=cb,
        )
        state.sources = sources
        await cb("Stage 2/5", f"Discovered {len(sources)} sources")
    except Exception as e:
        msg = f"Stage 2 failed: {e}"
        logger.error(msg)
        state.stage_errors.append(msg)
        raise RuntimeError(msg) from e

    out_root = output_dir or settings.outputs_dir
    run_store = RunStore(run_id=state.run_id, base_dir=out_root)
    loop = asyncio.get_running_loop()

    try:
        with scoped_run_store(run_store), scoped_progress_cb(cb):
            # ── Stage 3: Collect Comments ─────────────────────────────────────
            try:
                await cb("Stage 3/5", f"Collecting comments from {len(state.sources)} sources…")
                raw_comments, updated_sources = await collect_all(
                    sources=state.sources, progress_cb=cb
                )
                state.sources = updated_sources
                if run_store.raw_count() > 0:
                    if raw_comments:
                        run_store.add_raw_batch(raw_comments)
                        raw_comments = []
                    state.raw_comments = []
                    state.total_comments_raw = run_store.raw_count()
                else:
                    state.raw_comments = raw_comments
                    state.total_comments_raw = len(raw_comments)
                await cb("Stage 3/5", f"Collected {state.total_comments_raw:,} raw comments")
            except Exception as e:
                msg = f"Stage 3 failed: {e}"
                logger.error(msg)
                state.stage_errors.append(msg)
                raise RuntimeError(msg) from e

            if state.total_comments_raw == 0:
                from core.schemas import RawComment
                fallback_comments: list[RawComment] = []
                for i, src in enumerate(state.sources):
                    txt = (src.snippet or "").strip()
                    if txt and len(txt) >= 15 and src.title != "[Original Input]":
                        fallback_comments.append(
                            RawComment(
                                comment_id=f"snip_{i}",
                                source_url=src.url,
                                platform=src.platform,
                                author="Web Snippet",
                                original_text=txt,
                            )
                        )
                if fallback_comments:
                    logger.info(f"Using {len(fallback_comments)} real discovered web snippets because collectors returned 0 comments.")
                    state.raw_comments = fallback_comments
                    state.total_comments_raw = len(fallback_comments)
                else:
                    err_reasons = [
                        f"{s.platform}: {s.error_message}"
                        for s in state.sources
                        if s.error_message
                    ]
                    reason_note = "; ".join(err_reasons[:3]) or "No public comments found or comments disabled"
                    warn_msg = f"Stage 3 collected 0 real comments ({reason_note})"
                    logger.warning(warn_msg)
                    state.stage_errors.append(warn_msg)

            # ── Stage 4: AI Analysis ──────────────────────────────────────────
            try:
                await cb("Stage 4/5", "Running Map-Reduce AI sentiment analysis…")
                use_streamed = run_store.raw_count() > 0
                if use_streamed:
                    executive, ui_preview, exact_stats = await map_reduce_streamed(
                        run_store=run_store,
                        topic=state.topic,
                        input_url=input_url,
                        progress_cb=cb,
                    )
                    state.analyzed_comments = ui_preview
                    state.executive_review = executive
                    state.analytics_stats = exact_stats
                    state.total_comments_valid = int(exact_stats.get("total", len(ui_preview)))
                    state.positive_pct = float(exact_stats.get("positive_pct", 0.0))
                    state.neutral_pct = float(exact_stats.get("neutral_pct", 0.0))
                    state.negative_pct = float(exact_stats.get("negative_pct", 0.0))
                else:
                    analyzed = await map_analyze(
                        raw_comments=state.raw_comments,
                        topic=state.topic,
                        progress_cb=cb,
                    )
                    executive, valid_comments = await reduce_synthesize(
                        analyzed=analyzed,
                        topic=state.topic,
                        input_url=input_url,
                        progress_cb=cb,
                    )
                    state.analyzed_comments = analyzed
                    state.executive_review = executive
                    state.total_comments_valid = len(analyzed)
                    if len(state.raw_comments) > 2000:
                        import gc
                        state.raw_comments = []
                        gc.collect()

                    if valid_comments:
                        total = len(valid_comments)
                        state.positive_pct = round(
                            sum(1 for c in valid_comments if c.sentiment == "Positive") / total * 100, 1
                        )
                        state.neutral_pct = round(
                            sum(1 for c in valid_comments if c.sentiment == "Neutral") / total * 100, 1
                        )
                        state.negative_pct = round(
                            sum(1 for c in valid_comments if c.sentiment == "Negative") / total * 100, 1
                        )

                await cb(
                    "Stage 4/5",
                    f"Analysis complete — Score: {executive.ai_score_out_of_10:.1f}/10, "
                    f"Sentiment: {executive.overall_sentiment}",
                )
            except Exception as e:
                msg = f"Stage 4 failed: {e}"
                logger.error(msg)
                state.stage_errors.append(msg)
                raise RuntimeError(msg) from e

            # ── Stage 5: Excel Report ─────────────────────────────────────────
            try:
                await cb("Stage 5/5", "Building Excel report…")

                def _excel_progress(msg: str) -> None:
                    try:
                        loop.call_soon_threadsafe(asyncio.ensure_future, cb("Stage 5/5", msg))
                    except RuntimeError:
                        pass

                has_streamed_analyzed = run_store.analyzed_count() > 0
                xlsx_path = await asyncio.to_thread(
                    build_excel,
                    review=state.executive_review,
                    sources=state.sources,
                    analyzed_comments=state.analyzed_comments,
                    output_dir=out_root,
                    run_store=(run_store if has_streamed_analyzed else None),
                    exact_stats=(state.analytics_stats or None),
                    progress_fn=_excel_progress,
                )
                state.output_xlsx_path = xlsx_path
                await cb("Stage 5/5", f"Report ready: {xlsx_path}")
            except Exception as e:
                msg = f"Stage 5 failed: {e}"
                logger.error(msg)
                state.stage_errors.append(msg)
                raise RuntimeError(msg) from e
    finally:
        run_store.cleanup()

    logger.info(
        f"Pipeline COMPLETE — run_id={state.run_id}, "
        f"comments={len(state.analyzed_comments)}, xlsx={state.output_xlsx_path}"
    )
    return state
