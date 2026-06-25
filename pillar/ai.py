"""
Pillar AI — native Pydantic-to-LLM structured extraction.

Usage::

    from pillar.ai import PillarAI
    from pydantic import BaseModel

    class UserIntent(BaseModel):
        action: str
        entity: str
        confidence: float

    ai = PillarAI()                         # reads PILLAR_LLM_* env vars

    result: UserIntent = await ai.extract(
        prompt="The user wants to delete their account",
        model=UserIntent,
    )
    # result.action == "delete"
    # result.entity == "account"

Supports: OpenAI, Anthropic, any OpenAI-compatible endpoint.
LLM Circuit Breaker: automatic fallback on rate-limit / API error.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Raised when all LLM providers fail."""


class CircuitBreaker:
    """
    Simple Rust-inspired circuit breaker for LLM calls.

    States: CLOSED → OPEN (after N failures) → HALF_OPEN (after cooldown)
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 30) -> None:
        self._failures      = 0
        self._threshold     = failure_threshold
        self._cooldown      = cooldown_seconds
        self._opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at > self._cooldown:
            self._opened_at = None  # half-open: allow one probe
            return False
        return True

    def record_success(self) -> None:
        self._failures  = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()


class PillarAI:
    """
    Native Pydantic-to-LLM extraction engine.

    Configuration via environment variables::

        PILLAR_LLM_PROVIDER   = openai | anthropic | openai_compatible
        PILLAR_LLM_API_KEY    = sk-...
        PILLAR_LLM_MODEL      = gpt-4o-mini  (default)
        PILLAR_LLM_BASE_URL   = https://api.openai.com/v1  (optional override)
        PILLAR_LLM_FALLBACK   = gpt-3.5-turbo  (model used when primary fails)

    Usage::

        ai = PillarAI()

        result = await ai.extract(
            prompt="Parse this support ticket: ...",
            model=SupportTicket,
            system="You are a support analyst.",
            retries=3,
        )
    """

    def __init__(
        self,
        provider: str = None,
        api_key:  str = None,
        model:    str = None,
        base_url: str = None,
        fallback_model: str = None,
        failure_threshold: int = 3,
        cooldown_seconds:  int = 30,
    ) -> None:
        self.provider  = provider  or os.getenv("PILLAR_LLM_PROVIDER",  "openai")
        self.api_key   = api_key   or os.getenv("PILLAR_LLM_API_KEY",   "")
        self.model     = model     or os.getenv("PILLAR_LLM_MODEL",     "gpt-4o-mini")
        self.base_url  = base_url  or os.getenv("PILLAR_LLM_BASE_URL",  "")
        self.fallback  = fallback_model or os.getenv("PILLAR_LLM_FALLBACK", "gpt-3.5-turbo")
        self._cb       = CircuitBreaker(failure_threshold, cooldown_seconds)
        self._cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        prompt: str,
        model: Type[T],
        system: str = None,
        retries: int = 2,
        cache: bool = False,
    ) -> T:
        """
        Send *prompt* to the configured LLM and parse the response into
        *model* (a Pydantic BaseModel subclass).

        Pillar handles:
          - JSON schema injection into the system prompt
          - Automatic retry with exponential back-off
          - Circuit breaker: falls back to *fallback_model* on repeated failure
          - Optional response caching (in-process, keyed by prompt + model)

        Raises ``LLMError`` only if ALL retries + fallback fail.
        """
        cache_key = f"{prompt}:{model.__name__}"
        if cache and cache_key in self._cache:
            return self._cache[cache_key]

        schema = model.model_json_schema()
        system_prompt = (
            (system or "You are a precise data extraction assistant.") +
            f"\n\nRespond ONLY with valid JSON that conforms to this schema:\n"
            f"{json.dumps(schema, indent=2)}\n\nReturn nothing else — no explanation, no markdown."
        )

        last_error: Exception = RuntimeError("No attempts made")
        current_model = self.model

        for attempt in range(retries + 1):
            if self._cb.is_open:
                current_model = self.fallback

            try:
                raw = await self._call_llm(system_prompt, prompt, current_model)
                parsed = self._parse(raw, model)
                self._cb.record_success()
                if cache:
                    self._cache[cache_key] = parsed
                return parsed

            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                # Bad JSON or schema mismatch — retry is useful
                if attempt < retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))

            except Exception as exc:
                last_error = exc
                self._cb.record_failure()
                if self._cb.is_open and current_model != self.fallback:
                    current_model = self.fallback
                    continue
                if attempt < retries:
                    await asyncio.sleep(1.0 * (2 ** attempt))

        raise LLMError(
            f"PillarAI.extract() failed after {retries + 1} attempts: {last_error}"
        ) from last_error

    async def complete(self, prompt: str, system: str = None) -> str:
        """Free-form text completion (no structured parsing)."""
        return await self._call_llm(
            system or "You are a helpful assistant.",
            prompt,
            self.model,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _call_llm(self, system: str, user: str, model: str) -> str:
        if self.provider == "anthropic":
            return await self._anthropic(system, user, model)
        return await self._openai_compat(system, user, model)

    async def _openai_compat(self, system: str, user: str, model: str) -> str:
        try:
            import httpx
        except ImportError:
            raise LLMError(
                "httpx is required for PillarAI: pip install httpx"
            )

        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system",  "content": system},
                {"role": "user",    "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _anthropic(self, system: str, user: str, model: str) -> str:
        try:
            import httpx
        except ImportError:
            raise LLMError("httpx is required: pip install httpx")

        headers = {
            "x-api-key":         self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        }
        payload = {
            "model":      model or "claude-haiku-4-5-20251001",
            "max_tokens": 1024,
            "system":     system,
            "messages":   [{"role": "user", "content": user}],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]

    @staticmethod
    def _parse(raw: str, model: Type[T]) -> T:
        raw = raw.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        return model.model_validate(json.loads(raw))
