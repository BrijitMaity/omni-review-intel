"""
Shared HTTP, cookie, rating, and comment deduplication helpers for Stage 3 collectors.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from config import settings
from core.schemas import DiscoveredSource, PlatformType, RawComment

try:
    from curl_cffi import CurlOpt
    from curl_cffi import requests as curl_requests

    _CURL_AVAILABLE = True
except ImportError:
    _CURL_AVAILABLE = False

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


DEFAULT_BROWSER_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def parse_cookie_string(raw: str | dict[str, str] | None) -> dict[str, str]:
    """Parse a raw browser Cookie header string ('k1=v1; k2=v2') into a dict."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if k and v}
    cookies: dict[str, str] = {}
    cleaned = raw.strip().strip('"').strip("'")
    if cleaned.lower().startswith("cookie:"):
        cleaned = cleaned.split(":", 1)[1].strip()
    for part in cleaned.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k:
                cookies[k] = v
    return cookies


class _ResilientSession:
    """Chrome-impersonated HTTP session with automatic httpx fallback on 403/429/503/errors."""

    def __init__(self, cookies: dict[str, str] | None = None, timeout: float = 15.0) -> None:
        self._timeout = float(timeout)
        self._seed_cookies = dict(cookies or {})
        self._primary = None
        if _CURL_AVAILABLE:
            self._primary = curl_requests.Session(
                impersonate="chrome120",
                timeout=self._timeout,
                curl_options={CurlOpt.IPRESOLVE: 1},
                cookies=self._seed_cookies,
            )
        elif _HTTPX_AVAILABLE:
            self._primary = httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                cookies=self._seed_cookies,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    )
                },
            )

    @property
    def cookies(self):
        return self._primary.cookies if self._primary is not None else self._seed_cookies

    def _collect_cookie_dict(self) -> dict[str, str]:
        merged = dict(self._seed_cookies)
        if self._primary is not None:
            try:
                for k, v in self._primary.cookies.items():
                    merged[str(k)] = str(v)
            except Exception:
                pass
        return merged

    def _request(self, method: str, url: str, **kwargs):
        if self._primary is None:
            raise RuntimeError("No HTTP client available (install curl_cffi or httpx)")

        primary_resp = None
        try:
            primary_resp = getattr(self._primary, method)(url, **kwargs)
            status = int(getattr(primary_resp, "status_code", 0) or 0)
            final_url = str(getattr(primary_resp, "url", url))
            if status == 200 and "sorry/index" not in final_url and (
                method != "get" or str(getattr(primary_resp, "text", "") or "").strip()
            ):
                return primary_resp
            needs_fallback = status in (0, 403, 429, 503) or "sorry/index" in final_url or (
                method == "get" and status == 200 and not str(getattr(primary_resp, "text", "") or "").strip()
            )
        except Exception:
            needs_fallback = True

        if needs_fallback and _HTTPX_AVAILABLE and _CURL_AVAILABLE:
            req_headers = dict(kwargs.get("headers") or {})
            req_headers.setdefault(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            )
            httpx_kwargs = {k: v for k, v in kwargs.items() if k != "curl_options"}
            httpx_kwargs["headers"] = req_headers
            req_timeout = float(httpx_kwargs.pop("timeout", self._timeout) or self._timeout)
            try:
                with httpx.Client(
                    timeout=req_timeout,
                    follow_redirects=True,
                    cookies=self._collect_cookie_dict(),
                ) as client:
                    fb_resp = getattr(client, method)(url, **httpx_kwargs)
                    fb_status = int(getattr(fb_resp, "status_code", 0) or 0)
                    fb_url = str(getattr(fb_resp, "url", url))
                    if (fb_status == 200 and "sorry/index" not in fb_url) or primary_resp is None:
                        return fb_resp
            except Exception:
                pass

        if primary_resp is not None:
            return primary_resp
        raise RuntimeError(f"HTTP {method.upper()} failed for {url}")

    def get(self, url: str, **kwargs):
        return self._request("get", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._request("post", url, **kwargs)

    def close(self) -> None:
        if self._primary is not None:
            try:
                self._primary.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def create_chrome_session(
    cookies: str | dict[str, str] | None = None,
    timeout: float | None = None,
):
    """Create a Chrome-impersonated IPv4 HTTP session (with automatic httpx fallback)."""
    if not _CURL_AVAILABLE and not _HTTPX_AVAILABLE:
        return None
    req_timeout = float(timeout or max(settings.request_timeout, 15))
    cookie_dict = parse_cookie_string(cookies)
    return _ResilientSession(cookies=cookie_dict, timeout=req_timeout)


def fetch_url_sync(
    url: str,
    headers: dict[str, str] | None = None,
    cookies: str | dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[int, str, str]:
    """Fetch URL synchronously using Chrome TLS impersonation, falling back to httpx on 403/429/block."""
    merged_headers = {**DEFAULT_BROWSER_HEADERS, **(headers or {})}
    req_timeout = float(timeout or max(settings.request_timeout, 15))
    cookie_dict = parse_cookie_string(cookies)

    status = 0
    text = ""
    final_url = url

    sess = create_chrome_session(cookies=cookie_dict, timeout=req_timeout)
    if sess is not None:
        try:
            resp = sess.get(url, headers=merged_headers)
            status = int(resp.status_code)
            text = str(resp.text or "")
            final_url = str(getattr(resp, "url", url))
        except Exception:
            status = 0
        finally:
            try:
                sess.close()
            except Exception:
                pass

    should_fallback = (
        status in (0, 403, 429, 503)
        or "sorry/index" in final_url
        or (status == 200 and not text.strip())
    )
    if should_fallback and _HTTPX_AVAILABLE and (_CURL_AVAILABLE or sess is None):
        httpx_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            **merged_headers,
        }
        try:
            with httpx.Client(
                timeout=req_timeout,
                follow_redirects=True,
                cookies=cookie_dict,
                headers=httpx_headers,
            ) as client:
                r2 = client.get(url)
                st2 = int(r2.status_code)
                tx2 = str(r2.text or "")
                fu2 = str(getattr(r2, "url", url))
                if st2 == 200 and tx2.strip() and "sorry/index" not in fu2:
                    return st2, tx2, fu2
                if status == 0:
                    return st2, tx2, fu2
        except Exception:
            pass

    return status, text, final_url


def clamp_rating(value: Any) -> float | None:
    """Parse a rating into [0.0, 5.0] or return None if invalid/out of range."""
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        m = re.search(r"(\d+(?:\.\d+)?)", str(value))
        if not m:
            return None
        num = float(m.group(1))
    if 0.0 <= num <= 5.0:
        return round(num, 2)
    return None


def format_timestamp(ts: Any) -> str:
    """Normalize unix timestamps or date strings into clean YYYY-MM-DD / compact text."""
    if ts is None or ts == "":
        return ""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    text = str(ts).strip()
    if text.isdigit() and len(text) >= 9:
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    return text[:32]


def append_unique_comment(
    comments: list[RawComment],
    seen_keys: set[str],
    comment_id: str,
    author: str,
    text: str,
    published_date: Any,
    rating: Any,
    platform: PlatformType,
    source: DiscoveredSource,
    min_len: int = 2,
) -> bool:
    """Deduplicate by ID and normalized (author, text) before appending RawComment."""
    clean_text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(clean_text) < min_len:
        return False

    clean_author = (author or "Anonymous").strip() or "Anonymous"
    id_key = f"id:{comment_id}" if comment_id else ""
    text_key = f"txt:{clean_author.lower()}::{clean_text.lower()}"
    if (id_key and id_key in seen_keys) or text_key in seen_keys:
        return False

    if id_key:
        seen_keys.add(id_key)
    seen_keys.add(text_key)

    comments.append(
        RawComment(
            comment_id=comment_id or f"{platform.lower()}_{len(comments)}",
            source_url=source.url,
            platform=platform,
            author=clean_author,
            published_date=format_timestamp(published_date),
            rating=clamp_rating(rating),
            is_target=source.is_target,
            original_text=clean_text,
        )
    )
    return True
