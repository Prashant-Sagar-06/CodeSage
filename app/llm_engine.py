"""
llm_engine.py
Handles all Groq API communication with retry logic and exponential backoff.
Uses llama-3.3-70b-versatile for high-quality code reviews.
"""

import asyncio
import logging
import os
from typing import Optional

from groq import AsyncGroq, RateLimitError, APIError

from app.prompt_builder import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Model configuration
MODEL = "llama-3.3-70b-versatile"
MAX_TOKENS = 4096
TEMPERATURE = 0.1        # Low temperature for consistent, deterministic reviews
MAX_RETRIES = 3
BASE_BACKOFF = 2.0       # seconds — doubles on each retry (2s → 4s → 8s)


class LLMEngine:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")
        self.client = AsyncGroq(api_key=api_key)

    async def review_file(
        self,
        messages: list[dict],
        attempt: int = 1,
    ) -> Optional[str]:
        """
        Send a file diff to Groq and return the raw JSON string response.

        Implements exponential backoff: waits 2s, 4s, 8s between retries.
        Returns None if all retries are exhausted.
        """
        try:
            logger.info(f"Calling Groq API (attempt {attempt}/{MAX_RETRIES}) — model: {MODEL}")

            response = await self.client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *messages,
                ],
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},  # Force JSON output
            )

            raw = response.choices[0].message.content
            logger.info(f"Groq API responded successfully ({len(raw)} chars)")
            return raw

        except RateLimitError as e:
            if attempt >= MAX_RETRIES:
                logger.error(f"Rate limit exceeded after {MAX_RETRIES} attempts: {e}")
                return None
            wait = BASE_BACKOFF ** attempt
            logger.warning(f"Rate limited. Retrying in {wait}s... (attempt {attempt}/{MAX_RETRIES})")
            await asyncio.sleep(wait)
            return await self.review_file(messages, attempt + 1)

        except APIError as e:
            if attempt >= MAX_RETRIES:
                logger.error(f"Groq API error after {MAX_RETRIES} attempts: {e}")
                return None
            wait = BASE_BACKOFF ** attempt
            logger.warning(f"API error: {e}. Retrying in {wait}s...")
            await asyncio.sleep(wait)
            return await self.review_file(messages, attempt + 1)

        except Exception as e:
            logger.error(f"Unexpected error calling Groq API: {e}")
            return None

    async def health_check(self) -> bool:
        """Verify Groq API connectivity with a minimal test call."""
        try:
            response = await self.client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": 'Reply with {"status": "ok"}'}],
                max_tokens=20,
                response_format={"type": "json_object"},
            )
            return "ok" in response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq health check failed: {e}")
            return False
