"""
config.py — Application configuration loaded from .env via Pydantic Settings.
All pipeline tuning parameters live here; nothing is hardcoded elsewhere.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import threading
from typing import Any, Iterator, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_scoped_overrides: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "omni_scoped_settings_overrides", default=None
)
_cancel_event_var: ContextVar[Optional[threading.Event]] = ContextVar(
    "omni_run_cancel_event", default=None
)
_run_store_var: ContextVar[Optional[Any]] = ContextVar(
    "omni_run_store", default=None
)
_progress_cb_var: ContextVar[Optional[Any]] = ContextVar(
    "omni_progress_cb", default=None
)

_BYPASS_SCOPED_ATTRS = frozenset({
    "model_fields",
    "model_computed_fields",
    "model_config",
    "model_extra",
    "model_fields_set",
})


class Settings(BaseSettings):
    """Central configuration for omni-review-intel."""

    def __getattribute__(self, item: str) -> Any:
        if not item.startswith("_") and item not in _BYPASS_SCOPED_ATTRS:
            overrides = _scoped_overrides.get()
            if overrides is not None and item in overrides:
                return overrides[item]
        return super().__getattribute__(item)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=False,
    )

    # ── LLM ─────────────────────────────────────────────────
    llm_provider: str = Field(default="gemini", description="'gemini' or 'openai'")
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    gemini_flash_model: str = Field(
        default="gemini-2.0-flash", description="Fast/cheap model for batch tagging"
    )
    gemini_pro_model: str = Field(
        default="gemini-2.0-flash", description="Reasoning model for executive review"
    )
    openai_api_key: str = Field(default="", description="OpenAI key (optional)")
    openai_batch_model: str = Field(default="gpt-4o-mini")
    openai_review_model: str = Field(default="gpt-4o")

    # ── API Keys ─────────────────────────────────────────────
    youtube_api_key: str = Field(default="", description="YouTube Data API v3 key")
    reddit_client_id: str = Field(default="", description="Reddit OAuth client ID")
    reddit_client_secret: str = Field(default="", description="Reddit OAuth secret")
    reddit_user_agent: str = Field(
        default="omni-review-intel/1.0", description="Reddit user-agent string"
    )
    tavily_api_key: str = Field(default="", description="Tavily search API key")
    serpapi_key: str = Field(default="", description="SerpApi key (optional)")
    apify_api_key: str = Field(default="", description="Apify key (optional)")
    instagram_session_id: str = Field(default="", description="Instagram sessionid cookie (optional, for full comment pagination)")
    amazon_cookie: str = Field(default="", description="Amazon Cookie header (optional, for /product-reviews/ pagination)")
    facebook_cookie: str = Field(default="", description="Facebook Cookie header (optional, for logged-in comment access)")

    # ── Pipeline Caps ────────────────────────────────────────
    max_discovered_urls: int = Field(
        default=15, description="Max target URLs to discover per run"
    )
    max_comments_per_url: int = Field(
        default=5000, description="Max comments scraped per URL (0 = uncapped)"
    )
    llm_batch_size: int = Field(
        default=30, description="Number of comments per LLM Map batch"
    )
    max_concurrent_scrapes: int = Field(
        default=8, description="Max parallel async scrape tasks"
    )
    request_timeout: int = Field(
        default=30, description="HTTP request timeout in seconds"
    )

    # ── Output ───────────────────────────────────────────────
    outputs_dir: str = Field(
        default="outputs", description="Directory for generated .xlsx files"
    )
    cache_db_path: str = Field(
        default="cache.db", description="SQLite cache database file path"
    )

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    @property
    def has_youtube(self) -> bool:
        return bool(self.youtube_api_key and self.youtube_api_key.strip())

    @property
    def has_reddit(self) -> bool:
        return bool(
            self.reddit_client_id and self.reddit_client_id.strip()
            and self.reddit_client_secret and self.reddit_client_secret.strip()
        )

    @property
    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key and self.tavily_api_key.strip())

    @property
    def has_serpapi(self) -> bool:
        return bool(self.serpapi_key and self.serpapi_key.strip())

    @property
    def has_apify(self) -> bool:
        return bool(self.apify_api_key and self.apify_api_key.strip())

    @property
    def has_instagram_session(self) -> bool:
        return bool(self.instagram_session_id and self.instagram_session_id.strip())

    @property
    def has_amazon_cookie(self) -> bool:
        return bool(self.amazon_cookie and self.amazon_cookie.strip())

    @property
    def has_facebook_cookie(self) -> bool:
        return bool(self.facebook_cookie and self.facebook_cookie.strip())

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.strip())


# Global singleton — import this everywhere
settings = Settings()


def has_scoped_override(*keys: str) -> bool:
    """Return True if a context-local settings override is active (all or specific keys)."""
    overrides = _scoped_overrides.get()
    if not overrides:
        return False
    if not keys:
        return True
    return any(k in overrides for k in keys)


@contextmanager
def scoped_settings(**overrides: Any) -> Iterator[None]:
    """Temporarily apply thread/async-local overrides without mutating global settings."""
    current = dict(_scoped_overrides.get() or {})
    cleaned = {k: v for k, v in overrides.items() if v is not None}
    current.update(cleaned)
    token = _scoped_overrides.set(current)
    try:
        yield
    finally:
        _scoped_overrides.reset(token)


@contextmanager
def scoped_cancel_event(ev: Optional[threading.Event]) -> Iterator[None]:
    """Bind a cooperative cancellation Event to the current thread/async context."""
    token = _cancel_event_var.set(ev)
    try:
        yield
    finally:
        _cancel_event_var.reset(token)


def get_cancel_event() -> Optional[threading.Event]:
    """Return the active cooperative cancellation Event if present."""
    return _cancel_event_var.get()


def is_run_cancelled() -> bool:
    """Return True if the active pipeline run has been cancelled."""
    ev = _cancel_event_var.get()
    return bool(ev is not None and ev.is_set())


@contextmanager
def scoped_run_store(store: Any) -> Iterator[None]:
    """Bind a per-run disk-backed RunStore to the current thread/async context."""
    token = _run_store_var.set(store)
    try:
        yield
    finally:
        _run_store_var.reset(token)


def get_run_store() -> Optional[Any]:
    """Return the active per-run RunStore if bound."""
    return _run_store_var.get()


@contextmanager
def scoped_progress_cb(cb: Any) -> Iterator[None]:
    """Bind an async progress callback cleanly to the current thread/async context."""
    token = _progress_cb_var.set(cb)
    try:
        yield
    finally:
        _progress_cb_var.reset(token)


def get_progress_cb() -> Optional[Any]:
    """Return the active async progress callback if bound."""
    return _progress_cb_var.get()


def get_container_memory_limit_mb() -> Optional[float]:
    """Return Linux cgroup memory limit in MiB when running in a memory-bounded container."""
    for cg_path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory.high"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            if not cg_path.is_file():
                continue
            raw = cg_path.read_text(encoding="utf-8").strip()
            if not raw or raw == "max":
                continue
            limit_bytes = int(raw)
            # Ignore effectively unlimited host values (>= 128 GiB)
            if 0 < limit_bytes < 128 * (1024**3):
                return limit_bytes / (1024.0 * 1024.0)
        except Exception:
            continue
    if os.environ.get("RAILWAY_SERVICE_ID") or os.environ.get("RAILWAY_PROJECT_ID"):
        return 1024.0
    return None


def effective_max_comments(is_target: bool = True) -> int:
    """Return per-source comment limit; target URLs stream uncapped when requested <= 0."""
    requested = int(settings.max_comments_per_url or 0)
    if requested > 0:
        return requested
    if is_target:
        # Uncapped mode streams to SQLite RunStore + write-only Excel so 300k+ fit in 1 GB RAM
        return 0
    limit_mb = get_container_memory_limit_mb()
    return 1_000 if (limit_mb is not None and limit_mb <= 2048.0) else 1_500
