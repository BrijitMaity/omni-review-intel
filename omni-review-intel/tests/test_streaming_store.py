"""Unit tests for RunStore SQLite spooling, streaming Stage 4, and write-only Excel streaming."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from openpyxl import load_workbook

from core.run_store import CompactIdSet, RunStore
from core.schemas import DiscoveredSource, RawComment, TopicProfile
from stages.s4_ai_analyzer import map_reduce_streamed
from stages.s5_excel_builder import build_excel


def test_compact_id_set_and_run_store_end_to_end(tmp_path):
    cset = CompactIdSet(["a", "b"])
    assert "a" in cset
    assert "b" in cset
    assert "missing" not in cset
    cset.add("c")
    assert len(cset) == 3

    store = RunStore(run_id="test_stream_01", base_dir=tmp_path)
    try:
        raws = [
            RawComment(
                comment_id=f"c_{i}",
                source_url="https://www.youtube.com/watch?v=FyS5dAywkEo" if i < 6 else "https://reddit.com/r/test",
                platform="YouTube" if i < 6 else "Reddit",
                author=f"user_{i}",
                published_date="1 day ago",
                is_target=(i < 6),
                original_text=(
                    f"Amazing song number {i} love it!" if i % 2 == 0
                    else f"Terrible sound glitch {i} awful!"
                ),
            )
            for i in range(8)
        ]
        assert store.add_raw_batch(raws) == 8
        assert store.raw_count() == 8

        topic = TopicProfile(
            canonical_name="LISA - SaWaDiKa",
            category="Music",
            input_type="video",
            search_queries=["LISA SaWaDiKa reviews", "LISA MV reactions", "LISA reception"],
        )

        with patch("stages._llm_client.get_llm_client", side_effect=RuntimeError("no key")):
            review, preview, stats = asyncio.run(
                map_reduce_streamed(
                    run_store=store,
                    topic=topic,
                    input_url="https://www.youtube.com/watch?v=FyS5dAywkEo",
                )
            )

        assert stats["total"] == 8
        assert stats["target_count"] == 6
        assert stats["discovered_count"] == 2
        assert len(preview) == 8

        sources = [
            DiscoveredSource(
                url="https://www.youtube.com/watch?v=FyS5dAywkEo",
                platform="YouTube",
                title="LISA",
                is_target=True,
                scrape_status="success",
                comments_collected=6,
            ),
            DiscoveredSource(
                url="https://reddit.com/r/test",
                platform="Reddit",
                title="Thread",
                is_target=False,
                scrape_status="success",
                comments_collected=2,
            ),
        ]
        xlsx_path = build_excel(
            review=review,
            sources=sources,
            analyzed_comments=preview,
            output_dir=str(tmp_path),
            run_store=store,
            exact_stats=stats,
        )

        wb = load_workbook(xlsx_path)
        assert wb.active.title == "All Reviews & Comments"
        assert "🎯 Target Reviews" in wb.sheetnames
        assert "🌐 Discovered Web Reviews" in wb.sheetnames
        assert wb["All Reviews & Comments"].max_row == 9  # 1 header + 8 rows
        assert wb["🎯 Target Reviews"].max_row == 7       # 1 header + 6 rows
        assert wb["🌐 Discovered Web Reviews"].max_row == 3  # 1 header + 2 rows
        assert wb["Executive Review"].cell(row=12, column=2).value == stats["positive"]
        wb.close()
    finally:
        store.cleanup()
        assert not store.db_path.exists()
