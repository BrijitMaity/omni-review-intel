"""
stages/_llm_client.py — Thin provider wrapper around Gemini and OpenAI.

Provides:
  - `get_llm_client()` → LLMClient singleton
  - `LLMClient.structured_generate(system, user, schema, model_tier)`
    → returns a Pydantic model instance with strict JSON validation
  - `LLMClient.generate(system, user, model_tier)` → raw string response
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional, Type, TypeVar

from loguru import logger
from pydantic import BaseModel
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config import has_scoped_override, settings

T = TypeVar("T", bound=BaseModel)


# ── JSON extraction helper ────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    """Strip markdown code fences and extract the first JSON object or array."""
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "").strip()
    # Find first { or [  — handle leading text before JSON
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            # Find matching closing bracket to extract full block
            return text[i:]
    return text


def _safe_parse(raw: str, schema: Type[T]) -> T:
    """Parse JSON string and validate against Pydantic schema with detailed error."""
    json_str = _extract_json(raw)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"LLM JSON parse error: {e}\nRaw snippet: {raw[:600]}")
        raise ValueError(f"LLM returned invalid JSON: {e}") from e
    try:
        return schema.model_validate(data)
    except Exception as e:
        logger.error(f"Schema validation error: {e}\nData: {str(data)[:400]}")
        raise ValueError(f"LLM output failed schema validation: {e}") from e


# ── Gemini Client ─────────────────────────────────────────────────────────────

class _GeminiClient:
    def __init__(self) -> None:
        self._mode = "rest"
        self._client = None
        self._types = None
        key = (settings.gemini_api_key or "").strip()
        if not key:
            self._mode = "none"
            return
        try:
            from google import genai
            from google.genai import types as genai_types
            self._client = genai.Client(api_key=key)
            self._types = genai_types
            self._mode = "sdk"
        except Exception as e:
            logger.debug(f"google-genai SDK not loaded ({e}), using direct REST fallback")
            self._mode = "rest"

    def _model(self, tier: str) -> str:
        return (
            settings.gemini_flash_model if tier == "flash" else settings.gemini_pro_model
        )

    async def _call_rest(self, model: str, system: str, user: str) -> str:
        """Direct REST fallback to Gemini API without external SDK dependencies."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.gemini_api_key}"
        payload: dict = {
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}

        import aiohttp
        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=settings.request_timeout),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Gemini REST API status {resp.status}: {text[:250]}")
                data = await resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
                return ""

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    async def generate(self, system: str, user: str, model_tier: str = "flash") -> str:
        loop = asyncio.get_event_loop()
        model_name = self._model(model_tier)

        if self._mode == "sdk" and self._client and self._types:
            client = self._client
            types = self._types

            def _call() -> str:
                response = client.models.generate_content(
                    model=model_name,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.1,
                        max_output_tokens=8192,
                    ),
                )
                return response.text or ""

            try:
                return await loop.run_in_executor(None, _call)
            except Exception as e:
                logger.debug(f"Gemini SDK call failed ({e}), falling back to direct REST")
                return await self._call_rest(model=model_name, system=system, user=user)
        else:
            return await self._call_rest(model=model_name, system=system, user=user)

    async def structured_generate(
        self, system: str, user: str, schema: Type[T], model_tier: str = "flash"
    ) -> T:
        raw = await self.generate(system=system, user=user, model_tier=model_tier)
        return _safe_parse(raw, schema)


# ── OpenAI Client ─────────────────────────────────────────────────────────────

class _OpenAIClient:
    def __init__(self) -> None:
        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        except ImportError as e:
            raise RuntimeError("openai not installed. Run: pip install openai") from e

    def _model(self, tier: str) -> str:
        return (
            settings.openai_batch_model if tier == "flash" else settings.openai_review_model
        )

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    async def generate(self, system: str, user: str, model_tier: str = "flash") -> str:
        model_name = self._model(model_tier)
        response = await self._client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=8192,
        )
        return response.choices[0].message.content or ""

    async def structured_generate(
        self, system: str, user: str, schema: Type[T], model_tier: str = "flash"
    ) -> T:
        raw = await self.generate(system=system, user=user, model_tier=model_tier)
        return _safe_parse(raw, schema)


# ── Unified LLMClient façade ──────────────────────────────────────────────────

class LLMClient:
    """Provider-agnostic LLM client. Uses Gemini by default, OpenAI if configured."""

    def __init__(self) -> None:
        provider = settings.llm_provider.lower()
        if provider == "openai" and settings.has_openai:
            logger.info("LLM provider: OpenAI")
            self._backend: _GeminiClient | _OpenAIClient = _OpenAIClient()
        elif settings.has_gemini:
            logger.info("LLM provider: Gemini")
            self._backend = _GeminiClient()
        elif settings.has_openai:
            logger.info("LLM provider: OpenAI (fallback)")
            self._backend = _OpenAIClient()
        else:
            raise RuntimeError(
                "No LLM API key found. Set GEMINI_API_KEY or OPENAI_API_KEY in .env"
            )

    async def generate(self, system: str, user: str, model_tier: str = "flash") -> str:
        return await self._backend.generate(
            system=system, user=user, model_tier=model_tier
        )

    async def structured_generate(
        self, system: str, user: str, schema: Type[T], model_tier: str = "flash"
    ) -> T:
        return await self._backend.structured_generate(
            system=system, user=user, schema=schema, model_tier=model_tier
        )


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: Optional[LLMClient] = None


def get_llm_client(force_refresh: bool = False) -> LLMClient:
    """Return (or create) the LLMClient, keeping scoped session keys isolated."""
    global _client
    if has_scoped_override("gemini_api_key", "openai_api_key", "llm_provider"):
        return LLMClient()
    if _client is None or force_refresh:
        _client = LLMClient()
    return _client
