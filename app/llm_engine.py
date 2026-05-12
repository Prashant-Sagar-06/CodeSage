"""
llm_engine.py
Updated to accept a system_prompt_override (for multilingual support — Feature #18).
"""

import asyncio
import logging
import os
from typing import Optional

from groq import AsyncGroq, RateLimitError, APIError

from app.prompt_builder import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
MAX_TOKENS = 4096
TEMPERATURE = 0.1
MAX_RETRIES = 3
BASE_BACKOFF = 2.0


class LLMEngine:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")
        self.client = AsyncGroq(api_key=api_key)

    async def review_file(
        self,
        messages: list[dict],
        system_prompt_override: Optional[str] = None,
        attempt: int = 1,
    ) -> Optional[str]:
        """
        Send a file diff to Groq and return the raw JSON string response.
        Accepts an optional system_prompt_override for multilingual support.
        Implements exponential backoff: 2s → 4s → 8s between retries.
        """
        system = system_prompt_override or SYSTEM_PROMPT

        try:
            logger.info(f"Calling Groq API (attempt {attempt}/{MAX_RETRIES})")

            response = await self.client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system},
                    *messages,
                ],
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content
            logger.info(f"Groq API responded ({len(raw)} chars)")
            return raw

        except RateLimitError as e:
            if attempt >= MAX_RETRIES:
                logger.error(f"Rate limit exceeded after {MAX_RETRIES} attempts: {e}")
                return None
            wait = BASE_BACKOFF ** attempt
            logger.warning(f"Rate limited. Retrying in {wait}s...")
            await asyncio.sleep(wait)
            return await self.review_file(messages, system_prompt_override, attempt + 1)

        except APIError as e:
            if attempt >= MAX_RETRIES:
                logger.error(f"Groq API error after {MAX_RETRIES} attempts: {e}")
                return None
            wait = BASE_BACKOFF ** attempt
            logger.warning(f"API error: {e}. Retrying in {wait}s...")
            await asyncio.sleep(wait)
            return await self.review_file(messages, system_prompt_override, attempt + 1)

        except Exception as e:
            logger.error(f"Unexpected error calling Groq API: {e}")
            return None

    async def health_check(self) -> bool:
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