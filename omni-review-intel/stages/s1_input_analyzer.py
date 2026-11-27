"""
stages/s1_input_analyzer.py — Stage 1: URL Classification & Entity Extraction.

Responsibilities:
  1. Classify the input URL type (product, video, image, article).
  2. Extract metadata via yt-dlp / crawl4ai / Pillow depending on type.
  3. Call the LLM to produce a TopicProfile (canonical name + search queries).
  4. If image URL and SerpApi key available: collect Google Lens visual-match URLs.
"""

from __future__ import annotations

import re
import asyncio
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from loguru import logger

from config import settings
from core.schemas import InputType, TopicProfile
from core.cache import get_cache

# ── Lazy imports for optional/heavy deps ─────────────────────────────────────

_YT_DLP_AVAILABLE = False
try:
    import yt_dlp  # noqa: F401
    _YT_DLP_AVAILABLE = True
except ImportError:
    pass

_TRANSCRIPT_AVAILABLE = False
YouTubeTranscriptApi = None  # type: ignore[assignment]
try:
    from youtube_transcript_api import YouTubeTranscriptApi  # noqa: F401
    _TRANSCRIPT_AVAILABLE = True
except ImportError:
    pass

_CRAWL4AI_AVAILABLE = False
try:
    from crawl4ai import AsyncWebCrawler  # noqa: F401
    _CRAWL4AI_AVAILABLE = True
except ImportError:
    pass

_SERPAPI_AVAILABLE = False
try:
    from serpapi import GoogleSearch  # noqa: F401
    _SERPAPI_AVAILABLE = True
except ImportError:
    pass


# ── URL classification regexes ────────────────────────────────────────────────

_YT_RE = re.compile(
    r"(youtube\.com/(watch|shorts|live|playlist|embed|v)|youtu\.be/)",
    re.IGNORECASE,
)
_IG_RE = re.compile(r"(?:instagram\.com|instagr\.am)", re.IGNORECASE)
_FB_VIDEO_RE = re.compile(
    r"(?:facebook\.com/(?:watch|reel|reels|videos|[^/]+/videos|[^/]+/reel)|fb\.watch/)",
    re.IGNORECASE,
)
_FB_RE = re.compile(r"(?:facebook\.com|fb\.watch|fb\.com|fb\.me)", re.IGNORECASE)
_TT_RE = re.compile(r"(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)", re.IGNORECASE)
_IMAGE_EXT_RE = re.compile(
    r"\.(jpg|jpeg|png|webp|gif|bmp|svg)(\?.*)?$", re.IGNORECASE
)
_PRODUCT_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product-reviews|product|item)/|/p/", re.IGNORECASE)
_PRODUCT_DOMAINS_RE = re.compile(
    r"(?:amazon\.|amzn\.|a\.co|flipkart\.|fkrt\.it|walmart\.com|bestbuy\.com|myntra\.com|ebay\.com|aliexpress\.com|target\.com|trustpilot\.com)",
    re.IGNORECASE,
)


def classify_url(url: str) -> InputType:
    """Classify a URL into one of: 'video', 'image', 'product', 'article'."""
    if _YT_RE.search(url) or _IG_RE.search(url) or _FB_VIDEO_RE.search(url) or _TT_RE.search(url):
        return "video"
    if _IMAGE_EXT_RE.search(url):
        return "image"
    if _PRODUCT_RE.search(url) or _PRODUCT_DOMAINS_RE.search(url):
        return "product"
    return "article"


async def _check_content_type(url: str, timeout: int = 10) -> str:
    """HEAD request to verify Content-Type of a URL."""
    try:
        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=connector) as sess:
            async with sess.head(
                url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True
            ) as resp:
                return resp.headers.get("Content-Type", "")
    except Exception:
        return ""


async def _fetch_yt_metadata_http(url: str) -> dict | None:
    """Fast YouTube metadata fallback via oEmbed + curl_cffi HTML tags when yt-dlp fails."""
    vid_m = re.search(
        r"(?:youtube\.com/(?:watch\?.*v=|shorts/|embed/|live/|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
        url,
    )
    if not vid_m:
        return None
    vid = vid_m.group(1)

    def _fetch() -> dict | None:
        import html as html_lib
        from stages.s3_collectors._http_utils import create_chrome_session

        sess = create_chrome_session(timeout=15)
        if sess is None:
            return None
        try:
            # Try oEmbed first (lightweight JSON with exact title and author_name)
            try:
                oe = sess.get(
                    f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json",
                    timeout=10,
                )
                if oe.status_code == 200:
                    data = oe.json()
                    title = (data.get("title") or "").strip()
                    if title:
                        return {
                            "title": title,
                            "description": "",
                            "tags": [],
                            "transcript_excerpt": "",
                            "uploader": (data.get("author_name") or "").strip(),
                        }
            except Exception:
                pass
            # Fallback to watch then embed HTML <title>
            for page_url in (
                f"https://www.youtube.com/watch?v={vid}",
                f"https://www.youtube.com/embed/{vid}",
            ):
                try:
                    resp = sess.get(page_url, timeout=15)
                    if resp.status_code == 200 and resp.text:
                        m_t = re.search(r"<title>(.*?)</title>", resp.text)
                        if m_t:
                            title = html_lib.unescape(m_t.group(1)).replace(" - YouTube", "").strip()
                            if title and title.lower() != "youtube":
                                return {
                                    "title": title,
                                    "description": "",
                                    "tags": [],
                                    "transcript_excerpt": "",
                                    "uploader": "",
                                }
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            try:
                sess.close()
            except Exception:
                pass
        return None

    return await asyncio.to_thread(_fetch)


def _extract_ig_headline(raw_title: str, raw_desc: str, uploader: str = "") -> tuple[str, str]:
    """Derive a concise readable title and clean uploader from Instagram meta/caption text."""
    import html as html_lib

    title = html_lib.unescape(raw_title or "").strip()
    desc = html_lib.unescape(raw_desc or "").strip()
    author = (uploader or "").strip()

    # Format: 'BBC News on Instagram: "Puerto Rico\'s Jennifer Barreto..."'
    m_og = re.match(r'^(.+?)\s+on Instagram:\s*["“]?(.+?)["”]?\s*$', title, re.DOTALL)
    if m_og:
        if not author:
            author = m_og.group(1).strip()
        title = m_og.group(2).strip()

    # Description format: '43K likes, 964 comments - bbcnews on March 26, 2026: "Caption..."'
    m_desc = re.match(
        r'^[\d,.KMB]+\s+likes?,\s*[\d,.KMB]+\s+comments?\s*-\s*([A-Za-z0-9_.]+)\s+on\s+[^:]+:\s*["“]?(.+?)["”]?\s*\.?\s*$',
        desc,
        re.DOTALL | re.IGNORECASE,
    )
    if m_desc:
        if not author:
            author = m_desc.group(1).strip()
        clean_caption = m_desc.group(2).strip()
        if clean_caption:
            desc = clean_caption

    is_generic = not title or title.lower().startswith("video by ") or title.lower() in ("instagram", "instagram post", "instagram video")
    if is_generic and not desc:
        return "", author
    candidate = desc if is_generic and desc else title
    candidate = re.sub(r"\s+", " ", candidate).strip().strip('"').strip("'")
    if len(candidate) > 95:
        cut = candidate[:95].rsplit(" ", 1)[0].rstrip(" ,;:-")
        candidate = (cut or candidate[:95]) + "…"

    return candidate, author


async def _fetch_ig_metadata_http(url: str) -> dict | None:
    """Fast Instagram post/reel metadata extraction via curl_cffi HTML meta tags & /embed/captioned/ fallback."""
    from stages.s3_collectors.instagram_collector import _extract_shortcode, _resolve_share_shortcode_sync

    shortcode = _extract_shortcode(url)
    resolved_url = url
    if not shortcode:
        shortcode, resolved_url = await asyncio.to_thread(_resolve_share_shortcode_sync, url)
    if not shortcode:
        return None

    def _fetch() -> dict | None:
        from bs4 import BeautifulSoup
        from stages.s3_collectors._http_utils import create_chrome_session

        sess = create_chrome_session(timeout=15)
        if sess is None:
            return None
        try:
            def _meta(html_text: str, attr_name: str, attr_val: str) -> str:
                p1 = re.search(
                    rf'<meta[^>]+{attr_name}=["\']{attr_val}["\'][^>]+content=["\']([^"\']*)["\']',
                    html_text,
                    re.IGNORECASE,
                )
                if p1:
                    return p1.group(1)
                p2 = re.search(
                    rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+{attr_name}=["\']{attr_val}["\']',
                    html_text,
                    re.IGNORECASE,
                )
                return p2.group(1) if p2 else ""

            candidates = []
            if resolved_url:
                candidates.append(resolved_url)
            candidates.extend([
                f"https://www.instagram.com/reel/{shortcode}/",
                f"https://www.instagram.com/p/{shortcode}/",
            ])
            seen_c: set[str] = set()
            for cand in candidates:
                if cand in seen_c:
                    continue
                seen_c.add(cand)
                resp = sess.get(cand)
                if resp.status_code != 200 or not resp.text:
                    continue
                final_u = str(getattr(resp, "url", cand))
                html_text = resp.text
                if "/accounts/login" in final_u or "PolarisLoginPage" in html_text:
                    continue
                og_title = _meta(html_text, "property", "og:title")
                desc = _meta(html_text, "name", "description") or _meta(html_text, "property", "og:description")
                title_tag_m = re.search(r"<title>(.*?)</title>", html_text, re.DOTALL | re.IGNORECASE)
                title_tag = title_tag_m.group(1) if title_tag_m else ""
                headline, author = _extract_ig_headline(og_title or title_tag, desc)
                if not headline:
                    # Try embedded JSON caption/owner text when og:title was generic 'Instagram'
                    m_cap = re.search(r'"caption"\s*:\s*\{[^{}]*"text"\s*:\s*"((?:\\.|[^"\\])+)"', html_text)
                    if m_cap:
                        try:
                            cap_decoded = json.loads(f'"{m_cap.group(1)}"')
                            m_user = re.search(r'"username"\s*:\s*"([A-Za-z0-9_.]+)"', html_text)
                            author = author or (m_user.group(1) if m_user else "")
                            headline, author = _extract_ig_headline("", cap_decoded, uploader=author)
                            desc = desc or cap_decoded
                        except Exception:
                            pass
                if headline or desc:
                    return {
                        "title": headline or f"Instagram Post ({shortcode})",
                        "description": desc[:2000],
                        "tags": [],
                        "transcript_excerpt": "",
                        "uploader": author,
                    }

            # Fallback to /embed/captioned/ when main post page redirects to login wall or omits meta tags
            emb_resp = sess.get(f"https://www.instagram.com/p/{shortcode}/embed/captioned/")
            if emb_resp.status_code == 200 and emb_resp.text:
                soup = BeautifulSoup(emb_resp.text, "html.parser")
                cap_el = soup.select_one(".Caption")
                usr_el = soup.select_one(".UsernameText, .HeaderText a, .CaptionUsername")
                usr_txt = usr_el.get_text(" ", strip=True) if usr_el else ""
                if cap_el:
                    for u_rm in cap_el.select(".CaptionUsername"):
                        if not usr_txt:
                            usr_txt = u_rm.get_text(" ", strip=True)
                        u_rm.decompose()
                cap_txt = cap_el.get_text(" ", strip=True) if cap_el else ""
                if cap_txt:
                    headline, author = _extract_ig_headline("", cap_txt, uploader=usr_txt)
                    return {
                        "title": headline or f"Instagram Post ({shortcode})",
                        "description": cap_txt[:2000],
                        "tags": [],
                        "transcript_excerpt": "",
                        "uploader": author or usr_txt,
                    }
        except Exception as e:
            logger.debug(f"Instagram HTTP metadata fallback failed for {url}: {e}")
        finally:
            try:
                sess.close()
            except Exception:
                pass
        return None

    return await asyncio.to_thread(_fetch)


def _fetch_youtube_transcript_sync(vid_id: str) -> str:
    """Fetch up to 120 transcript snippets across youtube-transcript-api v0.6 and v1+."""
    if not (_TRANSCRIPT_AVAILABLE and YouTubeTranscriptApi is not None and vid_id):
        return ""
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        entries = YouTubeTranscriptApi.get_transcript(vid_id)
    else:
        entries = YouTubeTranscriptApi().fetch(vid_id)
    parts: list[str] = []
    for entry in list(entries)[:120]:
        text = entry.get("text", "") if isinstance(entry, dict) else getattr(entry, "text", "")
        cleaned = str(text or "").strip()
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts)


async def _extract_video_metadata(url: str) -> dict:
    """Use yt-dlp to extract video metadata without downloading, with HTTP fallbacks."""
    if _IG_RE.search(url):
        ig_meta = await _fetch_ig_metadata_http(url)
        if ig_meta:
            return ig_meta

    if not _YT_DLP_AVAILABLE:
        http_meta = await _fetch_yt_metadata_http(url)
        if http_meta:
            return http_meta
        logger.warning("yt-dlp not available; metadata extraction limited")
        return {"title": url, "description": "", "tags": [], "transcript_excerpt": "", "uploader": ""}

    is_playlist = "playlist" in url.lower() or "list=" in url.lower()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True if is_playlist else False,
        "socket_timeout": settings.request_timeout,
        "nocheckcertificate": True,
        "extractor_args": {"youtube": {"player_client": ["mweb", "tv_embedded", "android", "ios", "web"]}},
    }
    try:
        def _extract() -> dict:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False) or {}

        info: dict = await asyncio.to_thread(_extract)
        raw_title = (info.get("title") or "").strip()
        if raw_title.lower().startswith("mix - "):
            raw_title = raw_title[6:].strip()

        if raw_title and raw_title != url:
            transcript_text = ""
            if _TRANSCRIPT_AVAILABLE and "youtube" in url.lower() and not is_playlist:
                try:
                    vid_id: str = info.get("id", "")
                    if vid_id:
                        transcript_text = await asyncio.to_thread(_fetch_youtube_transcript_sync, vid_id)
                except Exception as te:
                    logger.debug(f"Transcript fetch skipped: {te}")

            return {
                "title": raw_title,
                "description": (info.get("description") or "")[:2000],
                "tags": info.get("tags") or [],
                "transcript_excerpt": transcript_text[:3000],
                "uploader": info.get("uploader", "") or info.get("channel", ""),
            }
    except Exception as e:
        logger.debug(f"yt-dlp metadata extraction note for {url}: {e}")

    http_meta = await _fetch_yt_metadata_http(url)
    if http_meta:
        return http_meta
    if _FB_RE.search(url) or _TT_RE.search(url):
        text_meta = await asyncio.to_thread(_scrape_html_chrome_sync, url)
        if text_meta:
            meta_dict = {"title": url, "description": "", "tags": [], "transcript_excerpt": "", "uploader": ""}
            for ln in text_meta.splitlines():
                if ln.startswith("Title: "):
                    meta_dict["title"] = ln[7:].strip() or url
                elif ln.startswith("Uploader: "):
                    meta_dict["uploader"] = ln[10:].strip()
                elif ln.startswith("Description: "):
                    meta_dict["description"] = ln[13:].strip()
            return meta_dict
    return {"title": url, "description": "", "tags": [], "transcript_excerpt": "", "uploader": ""}


def _clean_flipkart_or_ecommerce_title(raw_title: str) -> str:
    """Strip marketplace boilerplate suffixes from HTML <title> tags."""
    import html as html_lib

    title = html_lib.unescape(raw_title or "").strip()
    title = re.sub(r"\s*Price\s+in\s+India\b.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(
        r"\s*(?:[:|]\s*Flipkart\.com|[:|-]\s*Amazon\.[a-z.]+|Reviews:\s*Latest Review of.*)$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s*(?:-\s*)?Buy\s+Online\s+at\s+Best\s+Price.*$", "", title, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", title).strip(" -|:")


def _scrape_html_chrome_sync(url: str) -> str:
    """Fast Chrome-impersonated HTML metadata + readable text extractor for Stage 1."""
    import html as html_lib
    from bs4 import BeautifulSoup
    from stages.s3_collectors._http_utils import create_chrome_session

    # Twitter / X direct FxTwitter JSON lookup for Stage 1
    m_tw = re.search(r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)", url, re.IGNORECASE)
    if m_tw:
        sess = create_chrome_session(timeout=12)
        if sess is not None:
            try:
                user, tid = m_tw.groups()
                r = sess.get(f"https://api.fxtwitter.com/{user}/status/{tid}", headers={"Accept": "application/json"})
                if r.status_code == 200:
                    tw = (r.json() or {}).get("tweet") or {}
                    text = str(tw.get("text") or "").strip()
                    author = ((tw.get("author") or {}).get("name") or user).strip()
                    if text:
                        headline = re.sub(r"\s+", " ", text)[:95].strip()
                        return f"Title: {headline}\nUploader: {author}\nDescription: {text}"
            except Exception:
                pass
            finally:
                try:
                    sess.close()
                except Exception:
                    pass

    sess = create_chrome_session(timeout=min(settings.request_timeout, 15))
    if sess is None:
        return ""
    try:
        resp = sess.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if resp.status_code != 200 or not resp.text:
            return ""
        low_body = resp.text.lower()
        if "validatecaptcha" in low_body or "enter the characters you see below" in low_body:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")

        def _meta(attr_name: str, attr_val: str) -> str:
            el = soup.find("meta", attrs={attr_name: re.compile(f"^{re.escape(attr_val)}$", re.I)})
            return html_lib.unescape(str(el.get("content") or "")).strip() if el else ""

        og_title = _meta("property", "og:title")
        og_desc = _meta("property", "og:description") or _meta("name", "description")
        prod_el = soup.select_one("#productTitle")
        h1_el = soup.select_one("h1")
        raw_title = (
            prod_el.get_text(" ", strip=True)
            if prod_el
            else (og_title or (soup.title.string.strip() if soup.title and soup.title.string else "") or (h1_el.get_text(" ", strip=True) if h1_el else ""))
        )

        # On Facebook posts, og:title is often the page name while og:description holds the story headline
        if _FB_RE.search(url) and og_desc:
            uploader = og_title if og_title and "log in" not in og_title.lower() else ""
            headline = re.sub(r"\s+", " ", og_desc).strip()
            if len(headline) > 95:
                cut = headline[:95].rsplit(" ", 1)[0].rstrip(" ,;:-")
                headline = (cut or headline[:95]) + "…"
            return f"Title: {headline}\nUploader: {uploader}\nDescription: {og_desc[:2000]}"

        clean_title = _clean_flipkart_or_ecommerce_title(raw_title)
        # Reject generic soft-error / login / JS-challenge placeholder titles
        if clean_title.lower() in (
            "sign in",
            "log in to facebook",
            "facebook",
            "instagram",
            "tiktok",
            "tiktok - make your day",
            "reddit - dive into anything",
            "amazon.com",
            "amazon.in",
            "robot check",
            "sorry! something went wrong!",
            "access denied",
            "just a moment...",
            "page not found",
            "buy products online at best price in india - all categories",
        ):
            clean_title = ""

        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        text = " ".join(soup.stripped_strings)
        if clean_title or og_desc or (len(text) > 100 and "tiktok - make your day" not in raw_title.lower()):
            return f"Title: {clean_title}\nDescription: {og_desc}\n\nContent:\n{text[:5000]}"
    except Exception as e:
        logger.debug(f"Chrome Stage 1 scrape failed for {url}: {e}")
    finally:
        try:
            sess.close()
        except Exception:
            pass
    return ""


async def _scrape_html_fallback(url: str) -> str:
    """Extract readable page metadata using Chrome TLS impersonation, falling back to aiohttp."""
    chrome_res = await asyncio.to_thread(_scrape_html_chrome_sync, url)
    if chrome_res:
        return chrome_res

    try:
        connector = aiohttp.TCPConnector()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=settings.request_timeout)) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html, "html.parser")
                        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                            tag.decompose()
                        title = soup.title.string.strip() if (soup.title and soup.title.string) else ""
                        title = _clean_flipkart_or_ecommerce_title(title)
                        text = " ".join(soup.stripped_strings)
                        return f"Title: {title}\n\nContent:\n{text[:5000]}"
                    except Exception:
                        return html[:3000]
    except Exception as e:
        logger.debug(f"HTML fallback failed for {url}: {e}")
    return f"URL: {url}"


async def _crawl_page_markdown(url: str) -> str:
    """Extract clean page metadata & text via Chrome HTTP first for e-commerce/social, then crawl4ai."""
    if _PRODUCT_DOMAINS_RE.search(url) or _FB_RE.search(url) or "x.com" in url.lower() or "twitter.com" in url.lower():
        fast = await _scrape_html_fallback(url)
        if fast and fast != f"URL: {url}" and not fast.startswith("Title: \n"):
            return fast

    if not _CRAWL4AI_AVAILABLE:
        return await _scrape_html_fallback(url)
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url)
            md = getattr(result, "markdown", None) or getattr(result, "markdown_v2", None) or ""
            if md and len(str(md).strip()) > 30:
                return str(md)[:6000]
    except Exception as e:
        logger.warning(f"crawl4ai failed for {url}: {e}")
    return await _scrape_html_fallback(url)


async def _download_image_bytes(url: str, max_bytes: int = 2_000_000) -> Optional[bytes]:
    """Download image bytes for multimodal LLM input, capped at max_bytes."""
    try:
        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=connector) as sess:
            async with sess.get(
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                return data[:max_bytes]
    except Exception as e:
        logger.warning(f"Image download failed for {url}: {e}")
        return None


async def _serpapi_lens_urls(image_url: str) -> list[str]:
    """Use Google Lens (SerpApi) to find visual-match URLs for an image."""
    if not (settings.has_serpapi and _SERPAPI_AVAILABLE):
        return []
    try:
        def _search() -> list[str]:
            params = {
                "engine": "google_lens",
                "url": image_url,
                "api_key": settings.serpapi_key,
            }
            search = GoogleSearch(params)
            results = search.get_dict()
            visual_matches = results.get("visual_matches", [])
            return [m.get("link", "") for m in visual_matches if m.get("link")]

        urls = await asyncio.to_thread(_search)
        return [u for u in urls if u][:10]
    except Exception as e:
        logger.warning(f"SerpApi Lens failed: {e}")
        return []


def _extract_title_from_text_or_url(content_text: str, url: str) -> str:
    """Heuristic extraction of product or topic title from text or URL slug."""
    raw_title = ""
    raw_desc = ""
    raw_uploader = ""
    h1_title = ""

    for line in content_text.splitlines():
        line_clean = line.strip()
        lower = line_clean.lower()
        if lower.startswith("title:"):
            candidate = line_clean.split(":", 1)[1].strip()
            if candidate and candidate != url and not candidate.startswith("http"):
                raw_title = candidate
        elif lower.startswith("description:"):
            raw_desc = line_clean.split(":", 1)[1].strip()
        elif lower.startswith("uploader:"):
            raw_uploader = line_clean.split(":", 1)[1].strip()
        elif line_clean.startswith("# ") and not h1_title:
            candidate = line_clean[2:].strip()
            if candidate and candidate != url and not candidate.startswith("http"):
                h1_title = candidate

    if raw_title:
        if raw_title.lower().startswith("video by ") and raw_desc:
            headline, _ = _extract_ig_headline(raw_title, raw_desc, raw_uploader)
            if headline:
                return headline
        if not raw_title.lower().startswith("video by "):
            return raw_title
    if h1_title:
        return h1_title
    if raw_title:
        return raw_title

    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]
    if "instagram" in parsed.netloc.lower() or "instagr.am" in parsed.netloc.lower():
        for i, p in enumerate(parts):
            if p.lower() in ("p", "reel", "reels", "tv") and i + 1 < len(parts):
                if i > 0 and parts[i - 1].lower() != "share":
                    return f"Instagram Post by {parts[i - 1]}"
                return f"Instagram Post ({parts[i + 1]})"
    if "tiktok.com" in parsed.netloc.lower():
        creators = [p for p in parts if p.startswith("@")]
        if creators:
            return f"TikTok Video by {creators[0]}"
    if ("x.com" in parsed.netloc.lower() or "twitter.com" in parsed.netloc.lower()) and len(parts) >= 3 and parts[1].lower() == "status":
        return f"X Post by @{parts[0]}"
    for i, p in enumerate(parts):
        if p.lower() in ("dp", "p", "product-reviews", "product", "item") and i > 0:
            slug = parts[i - 1]
            clean_title = re.sub(r"[-_]+", " ", slug).strip()
            if clean_title:
                return clean_title.title()
    if parts:
        slug = parts[-1]
        clean_title = re.sub(r"[-_]+", " ", slug).strip()
        if re.match(r"^[A-Za-z0-9]{10}$", clean_title) and "amazon" in parsed.netloc:
            return f"Amazon Item {clean_title}"
        if len(clean_title) > 3:
            return clean_title.title()

    domain = parsed.netloc.replace("www.", "").split(".")[0].title()
    return f"{domain} Review Item" if domain else "Product Review Target"


def _fallback_topic_profile(content_text: str, input_type: InputType, url: str) -> TopicProfile:
    """Generate high-quality TopicProfile when LLM is unavailable or offline."""
    title = _extract_title_from_text_or_url(content_text, url)
    uploader = ""
    for line in content_text.splitlines():
        if line.lower().startswith("uploader:"):
            uploader = line.split(":", 1)[1].strip()
            break

    brand = ""
    known_brands = [
        "Sony", "Apple", "Samsung", "Bose", "Anker", "Dell", "HP", "Lenovo",
        "Nike", "Adidas", "Google", "Microsoft", "OnePlus", "Xiaomi", "Asus",
        "Logitech", "Sennheiser", "Canon", "Nikon", "PlayStation", "Xbox"
    ]
    # Whole-word check against title only (never match random query tokens like ?si=...hp...)
    for b in known_brands:
        if re.search(rf"\b{re.escape(b)}\b", title, re.IGNORECASE):
            brand = b
            break
    if not brand and uploader and input_type == "video":
        brand = uploader

    category = "Consumer Reviews"
    title_l = title.lower()
    if input_type == "video":
        category = "Video & Social Media"
    elif any(k in title_l for k in ("phone", "iphone", "galaxy", "pixel")):
        category = "Smartphones & Mobile Tech"
    elif any(k in title_l for k in ("headphone", "earbud", "audio", "speaker")):
        category = "Audio & Electronics"
    elif any(k in title_l for k in ("laptop", "computer", "pc", "monitor")):
        category = "Computers & Accessories"

    clean_q_topic = re.sub(r"[^\w\s&'.-]", " ", title)
    clean_q_topic = re.sub(
        r"\b(?:is\s+hiring|hiring|role|qualification|any\s+degree|freshers|apply\s+now|apply\s+link)\b",
        " ",
        clean_q_topic,
        flags=re.IGNORECASE,
    )
    clean_q_topic = re.sub(r"\s+", " ", clean_q_topic).strip()
    q_words = clean_q_topic.split()
    if len(q_words) > 6:
        q_words = q_words[:6]
    while len(q_words) > 2 and q_words[-1].lower() in {"with", "for", "and", "or", "in", "on", "of", "to", "by", "the", "a", "an"}:
        q_words.pop()
    clean_q_topic = " ".join(q_words)
    clean_q_topic = clean_q_topic or re.sub(r"\s+", " ", re.sub(r"[^\w\s&'.-]", " ", title)).strip() or title
    short_q = " ".join(q_words[:3]) if len(q_words) > 4 else clean_q_topic

    search_queries = [
        f"{short_q} review",
        f"{short_q} reddit worth it",
        f"{clean_q_topic} complaints problems",
        f"{short_q} youtube in depth review",
        f"{clean_q_topic} pros and cons",
    ]

    return TopicProfile(
        canonical_name=title,
        brand=brand,
        category=category,
        input_type=input_type,
        short_description=f"Review intelligence profile for {title}",
        search_queries=search_queries,
        visual_keywords=[title],
    )


async def _llm_extract_topic(
    content_text: str,
    input_type: InputType,
    url: str = "",
) -> TopicProfile:
    """Call LLM with entity_extraction prompt to produce a TopicProfile with fallback."""
    try:
        from stages._llm_client import get_llm_client

        prompt_path = Path("prompts/entity_extraction.txt")
        system_prompt = (
            prompt_path.read_text(encoding="utf-8")
            if prompt_path.exists()
            else (
                "You are an Entity & Topic Identification Specialist. "
                "Return ONLY valid JSON matching the TopicProfile schema."
            )
        )

        user_message = (
            f"Input type: {input_type}\n\n"
            f"Content:\n{content_text[:5000]}\n\n"
            "Return TopicProfile JSON with fields: canonical_name, brand, category, "
            f"input_type (must be exactly \"{input_type}\"), short_description, "
            "search_queries (5 items), visual_keywords."
        )

        client = get_llm_client()
        result = await client.structured_generate(
            system=system_prompt,
            user=user_message,
            schema=TopicProfile,
            model_tier="flash",
        )

        if result.input_type != input_type:
            result = result.model_copy(update={"input_type": input_type})

        return result
    except Exception as e:
        logger.warning(f"LLM entity extraction fallback used: {e}")
        return _fallback_topic_profile(content_text, input_type, url)


# ── Main Stage 1 Entry Point ──────────────────────────────────────────────────


def _is_generic_fallback_title(name: str, url: str = "") -> bool:
    """Return True if topic title is merely an ID/placeholder that should not poison cache."""
    cname = (name or "").strip()
    low = cname.lower()
    if not cname or low in (
        "instagram",
        "instagram post",
        "facebook",
        "tiktok",
        "tiktok - make your day",
        "reddit - dive into anything",
        "amazon.com",
        "amazon.in",
        "review item",
        "product review target",
    ):
        return True
    if low.startswith(("instagram post (", "amazon item ", "video by ", "tiktok video by ", "x post by ")):
        return True
    if len(cname) == 11 and cname.lower() in url.lower():
        return True
    if _IG_RE.search(url):
        from stages.s3_collectors.instagram_collector import _extract_shortcode
        _sc = _extract_shortcode(url)
        if _sc and low.replace(" ", "_") == _sc.lower():
            return True
    return False


async def analyze_input(
    url: str,
    progress_cb=None,
) -> tuple[TopicProfile, list[str]]:
    """
    Analyze the input URL and return (TopicProfile, extra_discovery_urls).

    Args:
        url:         The raw user-supplied URL.
        progress_cb: Optional async callback(stage_name, message).

    Returns:
        TopicProfile — canonical entity description + search queries.
        list[str]    — extra URLs from Google Lens (empty if not applicable).
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("URL cannot be empty")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    cache = get_cache()
    cache_key = f"v3::{url}"
    cached = cache.get("s1_topic", cache_key)
    if cached:
        topic_cached = TopicProfile.model_validate(cached)
        if not _is_generic_fallback_title(topic_cached.canonical_name, url):
            logger.info(f"Stage 1: cache hit for {url}")
            return topic_cached, []

    if progress_cb:
        await progress_cb("Stage 1", "Classifying URL type…")

    input_type: InputType = classify_url(url)

    # Confirm image type via Content-Type header check for ambiguous URLs
    if input_type not in ("video",):
        ct = await _check_content_type(url)
        if ct.startswith("image/"):
            input_type = "image"

    logger.info(f"Stage 1: URL classified as '{input_type}' — {url}")
    if progress_cb:
        await progress_cb("Stage 1", f"Input type: {input_type}. Extracting metadata…")

    extra_urls: list[str] = []
    content_text: str = ""

    if input_type == "video":
        meta = await _extract_video_metadata(url)
        content_text = (
            f"Title: {meta.get('title', '')}\n"
            f"Uploader: {meta.get('uploader', '')}\n"
            f"Description: {meta.get('description', '')}\n"
            f"Tags: {', '.join(meta.get('tags') or [])}\n"
            f"Transcript excerpt: {meta.get('transcript_excerpt', '')}"
        )

    elif input_type == "image":
        image_bytes = await _download_image_bytes(url)
        if image_bytes:
            content_text = (
                f"[Image URL]: {url}\n"
                f"[Image size]: {len(image_bytes)} bytes\n"
                "Analyze the visual content of this image URL to identify the product or topic."
            )
            extra_urls = await _serpapi_lens_urls(url)
        else:
            content_text = f"Image URL: {url}"

    else:  # product / article
        content_text = await _crawl_page_markdown(url)
        if not content_text or content_text == f"URL: {url}":
            content_text = f"Product/Article URL: {url}"

    if progress_cb:
        await progress_cb("Stage 1", "Calling LLM for entity identification…")

    topic = await _llm_extract_topic(content_text, input_type, url=url)

    logger.info(f"Stage 1: Identified topic '{topic.canonical_name}'")
    if not _is_generic_fallback_title(topic.canonical_name, url):
        cache.set("s1_topic", cache_key, topic.model_dump())

    return topic, extra_urls
