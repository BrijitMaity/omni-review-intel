"""
stages/s2_web_discovery.py — Stage 2: Web Discovery & URL Ranking.

Responsibilities:
  1. Execute the TopicProfile.search_queries concurrently via Tavily (primary),
     SerpApi Google Search (secondary), or DuckDuckGo (zero-key fallback).
  2. Merge any Google Lens visual-match URLs from Stage 1.
  3. Always keep the original input URL at index 0.
  4. Normalize URLs (strip utm_* tracking params), deduplicate canonically,
     filter junk pages, detect platform, and cap at MAX_DISCOVERED_URLS.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from loguru import logger

from config import settings
from core.schemas import DiscoveredSource, PlatformType, TopicProfile
from core.cache import get_cache

from functools import lru_cache

# ── Optional dependency guards ────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _try_import_tavily():
    try:
        from tavily import TavilyClient
        return TavilyClient
    except ImportError:
        return None

@lru_cache(maxsize=1)
def _try_import_serpapi():
    try:
        from serpapi import GoogleSearch
        return GoogleSearch
    except ImportError:
        return None

@lru_cache(maxsize=1)
def _try_import_duckduckgo():
    try:
        from duckduckgo_search import DDGS
        return DDGS
    except ImportError:
        return None


# ── URL Utilities ─────────────────────────────────────────────────────────────

_TRACKING_PARAMS = re.compile(
    r"^(utm_|fbclid|gclid|igsh|ig_rid|ref|referrer|source|campaign|medium|content|term)",
    re.IGNORECASE,
)
_JUNK_PATTERNS = re.compile(
    r"(?:(?:^|/)(?:login|signin|sign-in|signup|sign-up|register|welcome|sso|oauth|auth|privacy-policy|privacy|terms-of-(?:use|service)|cookie-policy|captcha|404|403)(?:/|$|\?)"
    r"|(?:whatsapp\.com|telegram\.me|t\.me|support\.google\.com|wikipedia\.org|wikidata\.org|britannica\.com|worldometers\.info|merriam-webster\.com|dictionary\.com|cambridge\.org|zhihu\.com|baidu\.com|csdn\.net|cnblogs\.com|jianshu\.com|bilibili\.com|getpaint\.net)"
    r"|https?://[^/]*\.gov(?:\.[a-z]{2})?(?:/|$)"
    r"|\.pdf$|\.xml$|^javascript:)",
    re.IGNORECASE,
)

_LOGIN_TITLE_RE = re.compile(
    r"\b(?:sign in to your account|log in to|passwordless|create an account|verify your identity)\b",
    re.IGNORECASE,
)
_BARE_LOCALE_PATH_RE = re.compile(
    r"^(?:(?:[a-z]{2}|india|global|apac|emea)(?:[-_][a-z]{2})?(?:/[a-z]{2}(?:[-_][a-z]{2})?)?|careers?(?:/[a-z]{2,8}(?:-[a-z]{2})?)?|about(?:-us)?|contact(?:-us)?|home)$",
    re.IGNORECASE,
)
_REVIEW_SIGNAL_RE = re.compile(
    r"\b(?:review|reviews|rating|ratings|forum|comment|comments|discuss|discussion|feedback|complaint|complaints|reddit|quora|ambitionbox|glassdoor|trustpilot|blog|pros|cons|worth|salary|experience)\b",
    re.IGNORECASE,
)


def _is_non_review_landing(url: str, title: str = "", snippet: str = "") -> bool:
    """Reject corporate SSO portals and bare homepage/careers landing pages lacking review signals."""
    if title and _LOGIN_TITLE_RE.search(title):
        return True
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    is_careers_root = (host.startswith(("careers.", "jobs.")) and path.count("/") <= 1)
    is_shallow_landing = (not path) or is_careers_root or bool(_BARE_LOCALE_PATH_RE.match(path))
    if is_shallow_landing and not _REVIEW_SIGNAL_RE.search(f"{url} {title}"):
        return True
    return False

_RELEVANCE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "you", "your",
    "any", "role", "post", "instagram", "youtube", "review", "reviews", "video",
    "official", "episode", "watch", "link", "full", "new", "2024", "2025", "2026",
})


def _topic_keywords(topic: TopicProfile) -> list[str]:
    """Extract normalized alphanumeric topic keywords for relevance gating."""
    raw = f"{topic.canonical_name} {topic.brand}".lower()
    tokens = [t for t in re.findall(r"[a-z0-9]{3,}", raw) if t not in _RELEVANCE_STOPWORDS]
    return list(dict.fromkeys(tokens))


def _is_relevant_result(url: str, title: str, snippet: str, keywords: list[str]) -> bool:
    """Require at least one topic keyword match when the topic has >= 2 distinct keywords."""
    if len(keywords) < 2:
        return True
    haystack = f"{url} {title} {snippet}".lower()
    return any(re.search(rf"\b{re.escape(kw)}", haystack) for kw in keywords)


def _strip_tracking(url: str) -> str:
    """Remove utm_* and other tracking query params from a URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    clean_qs = {k: v for k, v in qs.items() if not _TRACKING_PARAMS.match(k)}
    new_query = urlencode(
        {k: v[0] for k, v in clean_qs.items()}, doseq=False
    ) if clean_qs else ""
    cleaned = urlunparse(parsed._replace(query=new_query, fragment=""))
    return cleaned.rstrip("/")


def _canonical(url: str) -> str:
    """Return canonical key/URL for deduplication."""
    clean = _strip_tracking(url)
    try:
        from stages.s3_collectors.youtube_collector import _extract_video_id
        vid = _extract_video_id(clean)
        if vid:
            return f"youtube:{vid}"
    except Exception:
        pass
    try:
        from stages.s3_collectors.instagram_collector import _extract_shortcode
        sc = _extract_shortcode(clean)
        if sc:
            return f"instagram:{sc}"
    except Exception:
        pass
    try:
        from stages.s3_collectors.amazon_collector import _extract_asin
        asin = _extract_asin(clean)
        if asin:
            host = urlparse(clean).netloc.lower().lstrip("www.")
            return f"amazon:{host}:{asin}"
    except Exception:
        pass
    try:
        from stages.s3_collectors.flipkart_collector import _extract_flipkart_item_id
        fk_id = _extract_flipkart_item_id(clean)
        if fk_id:
            return f"flipkart:{fk_id}"
    except Exception:
        pass
    try:
        from stages.s3_collectors.facebook_collector import _extract_facebook_id
        fb_id = _extract_facebook_id(clean)
        if fb_id:
            return f"facebook:{fb_id}"
    except Exception:
        pass
    try:
        from stages.s3_collectors.tiktok_collector import _extract_tiktok_video_id
        tt_id = _extract_tiktok_video_id(clean)
        if tt_id:
            return f"tiktok:{tt_id}"
    except Exception:
        pass
    return clean.lower()


def _is_junk(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return True
    return bool(_JUNK_PATTERNS.search(url))


def _detect_platform(url: str) -> PlatformType:
    domain = urlparse(url).netloc.lower()
    if "youtube.com" in domain or "youtu.be" in domain:
        return "YouTube"
    if "reddit.com" in domain or "redd.it" in domain:
        return "Reddit"
    if "amazon." in domain or "amzn." in domain or domain == "a.co" or domain.endswith(".a.co"):
        return "Amazon"
    if "flipkart." in domain or "fkrt." in domain:
        return "Flipkart"
    if "facebook.com" in domain or "fb.watch" in domain or domain == "fb.com" or domain.endswith(".fb.com") or "fb.me" in domain:
        return "Facebook"
    if "instagram.com" in domain or "instagr.am" in domain:
        return "Instagram"
    if "tiktok.com" in domain:
        return "TikTok"
    if "twitter.com" in domain or domain == "x.com" or domain.endswith(".x.com"):
        return "Twitter"
    if any(w in domain for w in ("blog", "medium", "substack", "wordpress", "tomsguide", "soundguys")):
        return "Blog"
    if any(w in domain for w in ("forum", "quora", "stackexchange", "community", "gsmarena", "head-fi")):
        return "Forum"
    if any(w in domain for w in ("techcrunch", "theverge", "cnet", "bbc", "news", "engadget", "wired", "rtings")):
        return "News"
    if any(w in domain for w in ("myntra", "ebay", "walmart", "bestbuy", "trustpilot", "target", "nykaa", "meesho", "ajio", "croma", "aliexpress", "g2.com", "capterra")):
        return "ECommerce"
    return "Other"


def _make_source(url: str, title: str = "", snippet: str = "") -> DiscoveredSource:
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")
    return DiscoveredSource(
        url=_strip_tracking(url),
        domain=domain,
        platform=_detect_platform(url),
        title=title,
        snippet=snippet,
        scrape_status="pending",
    )


# ── Search Adapters ───────────────────────────────────────────────────────────

async def _search_tavily(query: str) -> list[dict]:
    TavilyClient = _try_import_tavily()
    if not (settings.has_tavily and TavilyClient):
        return []
    loop = asyncio.get_event_loop()
    try:
        def _call():
            client = TavilyClient(api_key=settings.tavily_api_key)
            resp = client.search(
                query=query, search_depth="basic", max_results=5, include_domains=[]
            )
            return resp.get("results", [])
        results = await loop.run_in_executor(None, _call)
        return [{"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("content", "")} for r in results]
    except Exception as e:
        logger.warning(f"Tavily search failed for '{query}': {e}")
        return []


async def _search_serpapi(query: str) -> list[dict]:
    GoogleSearch = _try_import_serpapi()
    if not (settings.has_serpapi and GoogleSearch):
        return []
    loop = asyncio.get_event_loop()
    try:
        def _call():
            params = {
                "engine": "google",
                "q": query,
                "num": 5,
                "api_key": settings.serpapi_key,
            }
            search = GoogleSearch(params)
            results = search.get_dict().get("organic_results", [])
            return [{"url": r.get("link", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")} for r in results]
        return await loop.run_in_executor(None, _call)
    except Exception as e:
        logger.warning(f"SerpApi search failed for '{query}': {e}")
        return []


async def _search_duckduckgo(query: str) -> list[dict]:
    DDGS = _try_import_duckduckgo()
    if not DDGS:
        return []
    loop = asyncio.get_event_loop()
    try:
        def _call():
            with DDGS() as ddgs:
                is_ascii = all(ord(ch) < 128 for ch in query)
                kwargs = {"region": "us-en", "max_results": 5} if is_ascii else {"max_results": 5}
                results = list(ddgs.text(query, **kwargs))
            return [{"url": r.get("href", ""), "title": r.get("title", ""), "snippet": r.get("body", "")} for r in results]
        return await loop.run_in_executor(None, _call)
    except Exception as e:
        logger.debug(f"DuckDuckGo search failed for '{query}': {e}")
        return []


async def _run_query(query: str) -> list[dict]:
    """Try Tavily → SerpApi → DuckDuckGo in order, returning first non-empty result."""
    clean_query = re.sub(r"[^\w\s&'.-]", " ", query or "")
    clean_query = re.sub(r"\s+", " ", clean_query).strip() or query
    for adapter in [_search_tavily, _search_serpapi, _search_duckduckgo]:
        results = await adapter(clean_query)
        if results:
            return results
    logger.warning(f"All search adapters returned empty for query: {clean_query!r}")
    return []


# ── Main Stage 2 Entry Point ──────────────────────────────────────────────────

async def discover_sources(
    topic: TopicProfile,
    input_url: str,
    extra_urls: list[str] | None = None,
    progress_cb=None,
) -> list[DiscoveredSource]:
    """
    Discover and normalize URLs for a given TopicProfile.

    Returns a deduplicated, platform-annotated list of DiscoveredSource objects
    capped at MAX_DISCOVERED_URLS, with the original input URL always at index 0.
    """
    topic_kw = _topic_keywords(topic)
    cache = get_cache()
    cache_key = f"s2_v4::{input_url}::{topic.canonical_name}::{settings.max_discovered_urls}"
    cached = cache.get("s2_sources", cache_key)
    if cached:
        logger.info("Stage 2: cache hit")
        raw_sources = [DiscoveredSource.model_validate(s) for s in cached]
        input_canon = _canonical(input_url)
        sources: list[DiscoveredSource] = []
        for i, s in enumerate(raw_sources):
            is_tgt = i == 0 or _canonical(s.url) == input_canon
            if not is_tgt and (
                _is_junk(s.url)
                or _is_non_review_landing(s.url, s.title, s.snippet)
                or not _is_relevant_result(s.url, s.title, s.snippet, topic_kw)
            ):
                continue
            s.is_target = is_tgt
            sources.append(s)
        return sources[: settings.max_discovered_urls]

    if progress_cb:
        await progress_cb("Stage 2", f"Running {len(topic.search_queries)} search queries…")

    # Execute all queries concurrently
    tasks = [_run_query(q) for q in topic.search_queries]
    results_per_query = await asyncio.gather(*tasks, return_exceptions=True)

    raw_results: list[dict] = []
    for r in results_per_query:
        if isinstance(r, list):
            raw_results.extend(r)

    # Build sources list — input URL always first
    seen_canonical: set[str] = set()
    sources: list[DiscoveredSource] = []
    input_platform = _detect_platform(input_url)
    _SINGLE_POST_PLATFORMS = {"YouTube", "Instagram", "Facebook", "TikTok"}
    input_canon = _canonical(input_url)

    def _add(url: str, title: str = "", snippet: str = "", is_target: bool = False) -> None:
        if not url or not url.startswith(("http://", "https://")):
            return
        if not is_target and (_is_junk(url) or _is_non_review_landing(url, title, snippet)):
            return
        if not is_target and (title or snippet) and not _is_relevant_result(url, title, snippet, topic_kw):
            logger.debug(f"Stage 2: Skipping off-topic search hit {url} ({title[:40]})")
            return
        canon = _canonical(url)
        # If the input URL is a specific social video/post, keep target post comments unmixed from unrelated posts
        if input_platform in _SINGLE_POST_PLATFORMS and _detect_platform(url) == input_platform and not is_target:
            logger.debug(f"Stage 2: Skipping secondary {input_platform} URL {url} to keep targeted post comments unmixed")
            return
        if canon in seen_canonical:
            return
        seen_canonical.add(canon)
        src = _make_source(url, title=title, snippet=snippet)
        src.is_target = is_target
        sources.append(src)

    _add(input_url, title="[Original Input]", is_target=True)

    # Extra visual-match URLs
    for eu in (extra_urls or []):
        _add(eu)

    # Search results
    for item in raw_results:
        _add(item.get("url", ""), title=item.get("title", ""), snippet=item.get("snippet", ""))

    # Cap
    sources = sources[: settings.max_discovered_urls]

    logger.info(f"Stage 2: discovered {len(sources)} unique sources")
    if progress_cb:
        await progress_cb("Stage 2", f"Discovered {len(sources)} sources.")

    cache.set("s2_sources", cache_key, [s.model_dump() for s in sources])
    return sources
