"""
config.py — Application configuration loaded from .env via Pydantic Settings.
All pipeline tuning parameters live here; nothing is hardcoded elsewhere.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_scoped_overrides: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "omni_scoped_settings_overrides", default=None
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
