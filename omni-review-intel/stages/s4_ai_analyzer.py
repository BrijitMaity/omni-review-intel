"""
stages/s4_ai_analyzer.py — Stage 4: Map-Reduce AI Analysis Engine.

MAP Phase:
  1. Pre-clean: strip empty, normalize whitespace, deduplicate exact text.
  2. Chunk into batches of LLM_BATCH_SIZE (default 30).
  3. Process all batches concurrently (semaphore-bounded) → AnalyzedComment list.

REDUCE Phase:
  4. Filter is_spam == True.
  5. Compute exact Python/pandas sentiment statistics.
  6. Sample representative comments per sentiment.
  7. Single LLM call → ExecutiveReview (Pydantic structured output).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from config import settings
from core.schemas import (
    AnalyzedComment,
    AspectSummary,
    BatchAnalysisResult,
    ExecutiveReview,
    RawComment,
    TopicProfile,
)
from core.cache import get_cache


# ── Map Phase ─────────────────────────────────────────────────────────────────


def _pre_clean(comments: list[RawComment]) -> list[RawComment]:
    """Strip empty, normalize whitespace, and deduplicate identical spam from the same author or duplicate IDs.
    Preserves unique comments from different authors.
    O(n) time.
    """
    seen_ids: set[str] = set()
    seen_author_text: set[tuple[str, str]] = set()
    clean: list[RawComment] = []
    for c in comments:
        text = " ".join(c.original_text.split())  # normalize whitespace
        if not text:
            continue
        if c.comment_id and c.comment_id in seen_ids:
            continue
        author_text = (c.author.lower().strip(), text.lower())
        if author_text in seen_author_text:
            continue
        if c.comment_id:
            seen_ids.add(c.comment_id)
        seen_author_text.add(author_text)
        clean.append(c.model_copy(update={"original_text": text}))
    logger.info(f"Pre-clean: {len(comments)} → {len(clean)} comments")
    return clean


def _chunk(lst: list, size: int):
    """Yield successive chunks of `size` from `lst`. O(1) per yield."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _heuristic_analyze_comment(
    raw: RawComment,
    topic_name: str,
) -> AnalyzedComment:
    """Accurate negation- and emoji-aware sentiment and aspect analysis when LLM is unavailable."""
    orig_text = raw.original_text
    text = orig_text.lower()

    pos_words = {
        "good", "great", "excellent", "love", "loved", "best", "amazing", "awesome",
        "impressed", "worth", "flawless", "superb", "happy", "recommend", "recommended",
        "smooth", "perfect", "fantastic", "clean", "easy", "well", "solid", "durable",
        "clear", "crisp", "punchy", "reliable", "fast", "stunned", "quiet", "nice", "top",
        "banger", "masterpiece", "beautiful", "fire", "goosebumps", "vibe", "fav",
        "favorite", "favourite", "super", "sukoon", "gem", "underrated", "pure",
        "legend", "soothing", "soulful", "addictive", "magic", "magical", "hit", "badiya",
        "mast", "kamaal", "zabardast", "shandar", "op", "hilarious", "funny", "enjoyed",
        "brilliant", "legendary", "crazy", "peak", "lit", "goat",
    }
    neg_words = {
        "bad", "terrible", "worst", "broken", "hate", "hated", "poor", "disappointed",
        "disappointing", "fail", "failed", "annoying", "issue", "issues", "problem",
        "problems", "defective", "expensive", "cheap", "horrible", "awful", "regret",
        "waste", "slow", "fragile", "loose", "ugly", "pain", "uncomfortable", "heavy",
        "trash", "boring", "flop", "dislike", "cringe", "overrated", "bakwas", "pathetic",
        "bekar", "ghatiya", "wahiyat", "overhyped", "mid", "useless", "sucks", "garbage",
        "scam", "fake", "fraud", "clickbait", "misleading", "error", "unavailable",
        "expired", "rejected", "unresponsive", "cheated", "delay", "delayed", "stuck",
        "toxic", "ruined", "unwatchable",
    }
    negators = {
        "not", "no", "never", "dont", "don't", "doesnt", "doesn't", "didnt", "didn't",
        "isnt", "isn't", "wasnt", "wasn't", "werent", "weren't", "cant", "can't",
        "cannot", "wont", "won't", "wouldnt", "wouldn't", "shouldnt", "shouldn't",
        "hardly", "barely", "neither", "nor", "without", "nahi", "mat",
    }

    pos_score = 0.0
    neg_score = 0.0
    tokens = re.findall(r"[a-z0-9']+", text)
    for idx, tok in enumerate(tokens):
        negated = any(prev in negators for prev in tokens[max(0, idx - 3) : idx])
        if tok in pos_words:
            if negated:
                neg_score += 1.0
            else:
                pos_score += 1.0
        elif tok in neg_words:
            if negated:
                pos_score += 0.5
            else:
                neg_score += 1.0

    # Emoji sentiment cues (common on YouTube, Instagram, and TikTok)
    pos_emojis = ("🔥", "❤️", "❤", "♥️", "💖", "😍", "🙌", "👏", "💯", "🥰", "🎉", "👌", "👍", "😂", "🤣")
    neg_emojis = ("👎", "🤮", "💩", "😡", "🤬", "💔")
    pos_score += sum(0.8 for em in pos_emojis if em in orig_text)
    neg_score += sum(1.0 for em in neg_emojis if em in orig_text)

    if pos_score > neg_score:
        sentiment = "Positive"
        conf = min(0.95, 0.70 + 0.05 * pos_score)
    elif neg_score > pos_score:
        sentiment = "Negative"
        conf = min(0.95, 0.70 + 0.05 * neg_score)
    else:
        sentiment = "Neutral"
        conf = 0.75

    if raw.rating is not None:
        if raw.rating >= 4.0:
            sentiment = "Positive"
            conf = max(conf, 0.85)
        elif raw.rating <= 2.0:
            sentiment = "Negative"
            conf = max(conf, 0.85)
        elif raw.rating == 3.0 and pos_score == neg_score:
            sentiment = "Neutral"

    # Aspect Detection (whole-word matching avoids false hits like "performance" -> "ANC")
    aspect_tag = "General"
    if re.search(r"\b(?:battery|batteries|charge|charging|charger|mah|runtime|backup)\b", text):
        aspect_tag = "Battery"
    elif re.search(r"\banc\b|noise cancel", text):
        aspect_tag = "ANC"
    elif re.search(r"\b(?:sound|bass|audio|mic|microphone|volume|treble|speaker|speakers|vocals|acoustic)\b", text):
        aspect_tag = "Sound Quality"
    elif re.search(r"\b(?:camera|cameras|photo|photos|lens|selfie|megapixel|video quality)\b", text):
        aspect_tag = "Camera"
    elif re.search(r"\b(?:comfort|comfortable|ergonomic|fit|ear|ears|headband|clamp|lightweight|cushion)\b", text):
        aspect_tag = "Comfort"
    elif re.search(r"\b(?:build|durability|hinge|hinges|plastic|metallic|material|sturdy|fragile|scratch)\b", text):
        aspect_tag = "Build Quality"
    elif re.search(r"\b(?:price|cost|expensive|cheap|worth|value|dollar|money|budget|overpriced)\b", text):
        aspect_tag = "Price"
    elif re.search(r"\b(?:design|look|looks|color|style|aesthetic|appearance)\b", text):
        aspect_tag = "Design"
    elif re.search(r"\b(?:screen|display|speed|fast|lag|lags|fps|software|performance|smooth|heating)\b", text):
        aspect_tag = "Performance"
    elif re.search(r"\b(?:delivery|shipping|packaging|support|service|refund|return|warranty|workplace|salary|culture|interview|hiring)\b", text):
        aspect_tag = "Service & Experience"
    elif re.search(r"\b(?:story|script|acting|episode|editing|lyrics|music|song|content|tutorial|explained|host|creator|comedy|joke)\b", text):
        aspect_tag = "Content & Creative"

    is_spam = False
    spam_reason = ""
    spam_patterns = (
        "t.me/", "wa.me/", "crypto profit", "invest now",
        "forex trading", "whatsapp number", "whatsapp group",
        "contact on whatsapp", "dm on whatsapp", "message on telegram",
        "earning app", "free followers"
    )
    if any(s in text for s in spam_patterns):
        is_spam = True
        spam_reason = "Promotional or bot link"

    return AnalyzedComment(
        comment_id=raw.comment_id,
        source_url=raw.source_url,
        platform=raw.platform,
        author=raw.author,
        published_date=raw.published_date,
        rating=raw.rating,
        is_target=raw.is_target,
        original_text=raw.original_text,
        original_language="English",
        english_text=raw.original_text,
        sentiment=sentiment,
        confidence_score=conf,
        aspect_tag=aspect_tag,
        is_spam=is_spam,
        spam_reason=spam_reason,
    )


async def _analyze_batch(
    batch: list[RawComment],
    topic_name: str,
    prompt_path: Path,
    llm,
) -> list[AnalyzedComment]:
    """Send one batch to the LLM Map phase and return AnalyzedComment list.
    Results are cached by sorted comment IDs to avoid duplicate LLM calls on retry.
    """
    target_map = {c.comment_id: c.is_target for c in batch}

    if not llm:
        analyzed = [_heuristic_analyze_comment(c, topic_name) for c in batch]
        for a in analyzed:
            a.is_target = target_map.get(a.comment_id, getattr(a, "is_target", False))
        return analyzed

    cache = get_cache()
    # Cache key: sorted IDs for order-independence
    batch_key = "|".join(sorted(c.comment_id for c in batch))
    cached = cache.get("s4_batch", batch_key)
    if cached:
        analyzed = [AnalyzedComment.model_validate(x) for x in cached.get("comments", [])]
        for a in analyzed:
            a.is_target = target_map.get(a.comment_id, getattr(a, "is_target", False))
        return analyzed

    system = (
        prompt_path.read_text(encoding="utf-8")
        if prompt_path.exists()
        else (
            "You are a multilingual sentiment/aspect analysis engine. "
            "Return ONLY valid JSON matching BatchAnalysisResult schema."
        )
    )
    system = system.replace("{topic_name}", topic_name)

    comments_payload = [
        {
            "comment_id": c.comment_id,
            "source_url": c.source_url,
            "platform": c.platform,
            "author": c.author,
            "published_date": c.published_date,
            "rating": c.rating,
            "original_text": c.original_text,
        }
        for c in batch
    ]
    user_msg = (
        f'Topic: "{topic_name}"\n\n'
        f"Comments (JSON array):\n{json.dumps(comments_payload, ensure_ascii=False)}\n\n"
        "Return BatchAnalysisResult JSON with a 'comments' array matching each input comment_id."
    )

    try:
        result: BatchAnalysisResult = await llm.structured_generate(
            system=system,
            user=user_msg,
            schema=BatchAnalysisResult,
            model_tier="flash",
        )
        analyzed = result.comments
        if not analyzed:
            raise ValueError("LLM returned empty comments array")
    except Exception as e:
        logger.warning(f"LLM batch analysis failed ({len(batch)} comments), using heuristic fallback: {e}")
        analyzed = [_heuristic_analyze_comment(c, topic_name) for c in batch]

    target_map = {c.comment_id: c.is_target for c in batch}
    for a in analyzed:
        a.is_target = target_map.get(a.comment_id, getattr(a, "is_target", False))

    cache.set("s4_batch", batch_key, {"comments": [a.model_dump() for a in analyzed]})
    return analyzed


async def map_analyze(
    raw_comments: list[RawComment],
    topic: TopicProfile,
    progress_cb=None,
) -> list[AnalyzedComment]:
    """MAP phase: clean + concurrent LLM batch analysis with heuristic fallback.

    Time complexity: O(n / batch_size) concurrent LLM calls, bounded by semaphore.
    """
    cleaned = _pre_clean(raw_comments)
    if not cleaned:
        logger.warning("No comments remain after pre-cleaning")
        return []

    try:
        from stages._llm_client import get_llm_client
        llm = get_llm_client()
    except Exception as e:
        logger.warning(f"No LLM client configured ({e}) — using fast heuristic analysis")
        llm = None

    prompt_path = Path("prompts/batch_comment_eval.txt")

    batches = list(_chunk(cleaned, settings.llm_batch_size))
    total_batches = len(batches)
    logger.info(f"Stage 4 Map: {len(cleaned)} comments → {total_batches} batches")

    sem = asyncio.Semaphore(4)

    async def _bounded_batch(batch: list[RawComment], idx: int) -> list[AnalyzedComment]:
        async with sem:
            result = await _analyze_batch(batch, topic.canonical_name, prompt_path, llm)
            step = 15 if llm is None else 5
            if llm is None and (idx + 1) % 25 == 0:
                await asyncio.sleep(0)
            if progress_cb and (idx == 0 or (idx + 1) % step == 0 or (idx + 1) == total_batches):
                await progress_cb(
                    "Stage 4 Map",
                    f"Analyzed batch {idx + 1}/{total_batches} ({min((idx + 1) * settings.llm_batch_size, len(cleaned))}/{len(cleaned)} comments)",
                )
            return result

    tasks = [_bounded_batch(b, i) for i, b in enumerate(batches)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_analyzed: list[AnalyzedComment] = []
    for r in results:
        if isinstance(r, BaseException):
            logger.error(f"Batch task raised exception: {r}")
        else:
            all_analyzed.extend(r)

    logger.info(f"Stage 4 Map complete: {len(all_analyzed)} analyzed comments")
    return all_analyzed


# ── Reduce Phase ──────────────────────────────────────────────────────────────


def _compute_stats(comments: list[AnalyzedComment]) -> dict:
    """Compute sentiment statistics overall and per platform using pandas.
    O(n) via vectorized pandas operations.
    """
    if not comments:
        return {
            "total": 0, "positive": 0, "neutral": 0, "negative": 0,
            "positive_pct": 0.0, "neutral_pct": 0.0, "negative_pct": 0.0,
            "platform_stats": {}, "aspect_stats": {},
        }

    # Build DataFrame only from needed columns (faster than model_dump for large sets)
    rows = [
        {
            "sentiment": c.sentiment,
            "platform": str(c.platform),
            "aspect_tag": c.aspect_tag,
        }
        for c in comments
    ]
    df = pd.DataFrame(rows)
    total = len(df)

    sentiment_counts = df["sentiment"].value_counts().to_dict()
    pos = int(sentiment_counts.get("Positive", 0))
    neu = int(sentiment_counts.get("Neutral", 0))
    neg = int(sentiment_counts.get("Negative", 0))

    # Per-platform breakdown
    platform_stats: dict = {}
    for platform, group in df.groupby("platform", sort=False):
        sc = group["sentiment"].value_counts().to_dict()
        platform_stats[str(platform)] = {
            "total": len(group),
            "positive": int(sc.get("Positive", 0)),
            "neutral": int(sc.get("Neutral", 0)),
            "negative": int(sc.get("Negative", 0)),
        }

    # Aspect frequencies
    aspect_stats: dict = {}
    for aspect, group in df.groupby("aspect_tag", sort=False):
        sc = group["sentiment"].value_counts().to_dict()
        total_a = len(group)
        aspect_stats[str(aspect)] = {
            "mention_count": total_a,
            "positive_pct": round(sc.get("Positive", 0) / total_a * 100, 1),
            "neutral_pct": round(sc.get("Neutral", 0) / total_a * 100, 1),
            "negative_pct": round(sc.get("Negative", 0) / total_a * 100, 1),
        }

    return {
        "total": total,
        "positive": pos,
        "neutral": neu,
        "negative": neg,
        "positive_pct": round(pos / total * 100, 1),
        "neutral_pct": round(neu / total * 100, 1),
        "negative_pct": round(neg / total * 100, 1),
        "platform_stats": platform_stats,
        "aspect_stats": aspect_stats,
    }


def _sample_comments(
    comments: list[AnalyzedComment], n: int = 10
) -> dict[str, list[str]]:
    """Sample representative comments per sentiment for Reduce context.
    O(n) single pass.
    """
    by_sentiment: dict[str, list[str]] = defaultdict(list)
    for c in comments:
        if len(by_sentiment[c.sentiment]) < n:
            by_sentiment[c.sentiment].append(c.english_text[:300])
    return dict(by_sentiment)


async def reduce_synthesize(
    analyzed: list[AnalyzedComment],
    topic: TopicProfile,
    input_url: str,
    progress_cb=None,
) -> tuple[ExecutiveReview, list[AnalyzedComment]]:
    """REDUCE phase: filter spam, compute stats, call LLM for ExecutiveReview.

    Returns:
        (ExecutiveReview, non_spam_comments)
    """
    from stages._llm_client import get_llm_client

    # Filter spam
    valid = [c for c in analyzed if not c.is_spam]
    logger.info(
        f"Stage 4 Reduce: {len(analyzed)} total → {len(valid)} valid (spam filtered)"
    )

    if not valid:
        logger.warning("No valid comments after spam filter — generating stub review")
        return _stub_review(topic, input_url), []

    stats = _compute_stats(valid)
    sample = _sample_comments(valid, n=10)

    # Build aspect summaries from computed stats
    aspect_summaries = [
        AspectSummary(
            aspect_name=asp,
            mention_count=data["mention_count"],
            positive_pct=data["positive_pct"],
            neutral_pct=data["neutral_pct"],
            negative_pct=data["negative_pct"],
        )
        for asp, data in sorted(
            stats["aspect_stats"].items(),
            key=lambda x: x[1]["mention_count"],
            reverse=True,
        )
    ]

    prompt_path = Path("prompts/executive_review.txt")
    system = (
        prompt_path.read_text(encoding="utf-8")
        if prompt_path.exists()
        else (
            "You are a Senior Consumer Research Analyst. "
            "Return ONLY valid JSON matching ExecutiveReview schema."
        )
    )
    system = system.replace("{topic_name}", topic.canonical_name)

    user_msg = (
        f'Topic: "{topic.canonical_name}"\n'
        f"Original URL: {input_url}\n\n"
        f"=== Aggregated Statistics ===\n{json.dumps(stats, indent=2)}\n\n"
        f"=== Representative Comments (sample) ===\n"
        f"{json.dumps(sample, indent=2, ensure_ascii=False)}\n\n"
        "Return ExecutiveReview JSON with ALL required fields: "
        "topic_name, original_url, overall_sentiment, ai_score_out_of_10, "
        "executive_summary, top_pros, top_cons, common_complaints, "
        "platform_comparison_notes, buying_or_action_verdict, aspect_summaries."
    )

    if progress_cb:
        await progress_cb("Stage 4 Reduce", "Generating Executive Review via LLM…")

    executive = None
    try:
        from stages._llm_client import get_llm_client
        llm = get_llm_client()
        executive = await llm.structured_generate(
            system=system,
            user=user_msg,
            schema=ExecutiveReview,
            model_tier="pro",
        )
        llm_takeaways = {
            a.aspect_name.lower().strip(): a.key_takeaway
            for a in executive.aspect_summaries
            if a.key_takeaway
        }
        merged_aspects = [
            a.model_copy(
                update={"key_takeaway": llm_takeaways.get(a.aspect_name.lower().strip(), a.key_takeaway)}
            )
            for a in aspect_summaries
        ]
        executive = executive.model_copy(update={"aspect_summaries": merged_aspects})
    except Exception as e:
        logger.warning(f"Executive review LLM unavailable or failed ({e}) — using data-backed synthesis")
        executive = _stub_review(topic, input_url, stats=stats, comments=valid)
        executive = executive.model_copy(update={"aspect_summaries": aspect_summaries})

    logger.info(
        f"Stage 4 Reduce complete: score={executive.ai_score_out_of_10}, "
        f"sentiment={executive.overall_sentiment}"
    )
    return executive, valid


async def map_reduce_streamed(
    run_store,
    topic: TopicProfile,
    input_url: str,
    progress_cb=None,
) -> tuple[ExecutiveReview, list[AnalyzedComment], dict]:
    """Chunked, constant-RAM Map-Reduce Stage 4 over a disk-backed RunStore."""
    from core.run_store import CompactIdSet

    total_raw = run_store.raw_count()
    if total_raw == 0:
        return _stub_review(topic, input_url), [], {}

    try:
        from stages._llm_client import get_llm_client
        llm = get_llm_client()
    except Exception as e:
        logger.warning(f"No LLM client configured ({e}) — using fast heuristic analysis")
        llm = None

    prompt_path = Path("prompts/batch_comment_eval.txt")
    seen_ids = CompactIdSet()
    seen_author_text = CompactIdSet()
    sem = asyncio.Semaphore(4)

    processed_raw = 0
    for chunk in run_store.iter_raw_chunks(chunk_size=3000):
        clean_chunk: list[RawComment] = []
        for c in chunk:
            text = " ".join(c.original_text.split())
            if not text:
                continue
            if c.comment_id and c.comment_id in seen_ids:
                continue
            at_key = f"{c.author.lower().strip()}\0{text.lower()}"
            if at_key in seen_author_text:
                continue
            if c.comment_id:
                seen_ids.add(c.comment_id)
            seen_author_text.add(at_key)
            clean_chunk.append(
                c.model_copy(update={"original_text": text})
                if text != c.original_text
                else c
            )

        if clean_chunk:
            batches = list(_chunk(clean_chunk, settings.llm_batch_size))

            async def _run_b(b: list[RawComment]) -> list[AnalyzedComment]:
                async with sem:
                    return await _analyze_batch(b, topic.canonical_name, prompt_path, llm)

            res_lists = await asyncio.gather(*[_run_b(b) for b in batches], return_exceptions=True)
            analyzed_chunk: list[AnalyzedComment] = []
            for r in res_lists:
                if isinstance(r, BaseException):
                    logger.error(f"Streamed batch task failed: {r}")
                else:
                    analyzed_chunk.extend(r)
            if analyzed_chunk:
                run_store.add_analyzed_batch(analyzed_chunk)

        processed_raw += len(chunk)
        if progress_cb:
            await progress_cb(
                "Stage 4 Map",
                f"Analyzed {processed_raw:,}/{total_raw:,} comments",
            )
        await asyncio.sleep(0)

    stats = run_store.compute_stats_sql()
    if not stats or stats.get("total", 0) == 0:
        return _stub_review(topic, input_url), [], stats or {}

    sample, ui_preview = run_store.sample_analyzed(limit_per_sentiment=10, ui_preview_limit=15000)

    aspect_summaries = [
        AspectSummary(
            aspect_name=asp,
            mention_count=data["mention_count"],
            positive_pct=data["positive_pct"],
            neutral_pct=data["neutral_pct"],
            negative_pct=data["negative_pct"],
        )
        for asp, data in sorted(
            stats["aspect_stats"].items(),
            key=lambda x: x[1]["mention_count"],
            reverse=True,
        )
    ]

    system_path = Path("prompts/executive_review.txt")
    system = (
        system_path.read_text(encoding="utf-8")
        if system_path.exists()
        else (
            "You are a Senior Consumer Research Analyst. "
            "Return ONLY valid JSON matching ExecutiveReview schema."
        )
    ).replace("{topic_name}", topic.canonical_name)

    prompt_stats = {
        k: stats[k]
        for k in (
            "total", "positive", "neutral", "negative",
            "positive_pct", "neutral_pct", "negative_pct",
            "platform_stats", "aspect_stats",
        )
        if k in stats
    }
    user_msg = (
        f'Topic: "{topic.canonical_name}"\n'
        f"Original URL: {input_url}\n\n"
        f"=== Aggregated Statistics ===\n{json.dumps(prompt_stats, indent=2)}\n\n"
        f"=== Representative Comments (sample) ===\n"
        f"{json.dumps(sample, indent=2, ensure_ascii=False)}\n\n"
        "Return ExecutiveReview JSON with ALL required fields: "
        "topic_name, original_url, overall_sentiment, ai_score_out_of_10, "
        "executive_summary, top_pros, top_cons, common_complaints, "
        "platform_comparison_notes, buying_or_action_verdict, aspect_summaries."
    )

    if progress_cb:
        await progress_cb("Stage 4 Reduce", "Generating Executive Review via LLM…")

    executive = None
    try:
        from stages._llm_client import get_llm_client
        llm_red = get_llm_client()
        executive = await llm_red.structured_generate(
            system=system,
            user=user_msg,
            schema=ExecutiveReview,
            model_tier="pro",
        )
        llm_takeaways = {
            a.aspect_name.lower().strip(): a.key_takeaway
            for a in executive.aspect_summaries
            if a.key_takeaway
        }
        merged_aspects = [
            a.model_copy(
                update={"key_takeaway": llm_takeaways.get(a.aspect_name.lower().strip(), a.key_takeaway)}
            )
            for a in aspect_summaries
        ]
        executive = executive.model_copy(update={"aspect_summaries": merged_aspects})
    except Exception as e:
        logger.warning(f"Executive review LLM unavailable or failed ({e}) — using data-backed synthesis")
        executive = _stub_review(topic, input_url, stats=stats, comments=ui_preview)
        executive = executive.model_copy(update={"aspect_summaries": aspect_summaries})

    return executive, ui_preview, stats


def _stub_review(
    topic: TopicProfile,
    input_url: str,
    stats: Optional[dict] = None,
    comments: Optional[list[AnalyzedComment]] = None,
) -> ExecutiveReview:
    """Return a data-driven ExecutiveReview based on aggregated stats when LLM is offline or unavailable."""
    if not stats or not comments:
        return ExecutiveReview(
            topic_name=topic.canonical_name,
            original_url=input_url,
            overall_sentiment="Neutral",
            ai_score_out_of_10=5.0,
            executive_summary=(
                f"Automated initial review profile for '{topic.canonical_name}'. "
                "Web discussions were scanned across available public platforms."
            ),
            top_pros=["Balanced general performance reported across web forums"],
            top_cons=["Check specific platform threads for detailed user issues"],
            buying_or_action_verdict=(
                f"Review overview generated for {topic.canonical_name}. Review the collected comments in Sheet 4 for details."
            ),
        )

    pos_pct = stats.get("positive_pct", 50.0)
    neu_pct = stats.get("neutral_pct", 25.0)
    neg_pct = stats.get("negative_pct", 25.0)
    total_valid = stats.get("total", len(comments))

    ai_score = round(min(10.0, max(1.0, (pos_pct * 0.08 + neu_pct * 0.04 + 1.0))), 1)
    if pos_pct >= 55.0:
        overall_sentiment = "Positive"
    elif neg_pct >= 40.0:
        overall_sentiment = "Negative"
    else:
        overall_sentiment = "Neutral"

    pos_comments = [c for c in comments if c.sentiment == "Positive"]
    neg_comments = [c for c in comments if c.sentiment == "Negative"]

    top_pros: list[str] = []
    if pos_comments:
        for c in pos_comments[:4]:
            t = c.original_text.strip().replace("\n", " ")
            if len(t) > 85:
                t = t[:82] + "..."
            if t and t not in top_pros:
                top_pros.append(t)
    if not top_pros:
        top_pros = [
            f"Favorable reception across community discussions ({pos_pct:.1f}% positive)",
            "Reliable core functionality reported by active users",
        ]

    top_cons: list[str] = []
    if neg_comments:
        for c in neg_comments[:3]:
            t = c.original_text.strip().replace("\n", " ")
            if len(t) > 85:
                t = t[:82] + "..."
            if t and t not in top_cons:
                top_cons.append(t)
    if not top_cons:
        top_cons = [
            f"Minor user concerns noted in {neg_pct:.1f}% of scanned discussions",
            "Individual user preferences vary by use-case",
        ]

    is_product = topic.input_type == "product"
    audience_noun = "customer reviews" if is_product else "public comments"
    summary_noun = "customer sentiment" if is_product else "audience sentiment"
    exec_summary = (
        f"Aggregated cross-web review analysis for '{topic.canonical_name}'. "
        f"Analyzed {total_valid:,} {audience_noun} and community discussions across web sources. "
        f"Overall {summary_noun} is {overall_sentiment} with an AI rating score of {ai_score:.1f}/10. "
        f"Sentiment distribution: {pos_pct:.1f}% positive, {neu_pct:.1f}% neutral, and {neg_pct:.1f}% negative."
    )

    if is_product:
        if pos_pct >= 60.0:
            verdict = (
                f"Recommended purchase. {topic.canonical_name} achieves a high positive rating ({pos_pct:.1f}%) "
                "with strong customer endorsement across online communities."
            )
        elif neg_pct >= 45.0:
            verdict = (
                f"Exercise caution before purchasing. Recurring user complaints ({neg_pct:.1f}% negative) indicate "
                "potential reliability or value trade-offs. Inspect the Bad Reviews and All Reviews tabs for details."
            )
        else:
            verdict = (
                f"Balanced option. {topic.canonical_name} receives mixed-to-favorable feedback. "
                "Best suited for buyers whose requirements match its highlighted strengths."
            )
    else:
        if pos_pct >= 60.0:
            verdict = (
                f"Strongly positive audience reception ({pos_pct:.1f}% positive). "
                f"Viewers and community commenters respond favorably to {topic.canonical_name}."
            )
        elif neg_pct >= 45.0:
            verdict = (
                f"Critical audience reception ({neg_pct:.1f}% negative). "
                "Review the Bad Reviews and All Reviews tabs for recurring viewer concerns and criticisms."
            )
        else:
            verdict = (
                f"Mixed-to-balanced public discussion around {topic.canonical_name} "
                f"({pos_pct:.1f}% positive, {neu_pct:.1f}% neutral, {neg_pct:.1f}% negative)."
            )

    return ExecutiveReview(
        topic_name=topic.canonical_name,
        original_url=input_url,
        overall_sentiment=overall_sentiment,
        ai_score_out_of_10=ai_score,
        executive_summary=exec_summary,
        top_pros=top_pros,
        top_cons=top_cons,
        common_complaints=[c for c in top_cons[:2]],
        platform_comparison_notes="Cross-platform sentiment aggregated across discovered web communities.",
        buying_or_action_verdict=verdict,
    )
