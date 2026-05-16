"""
Yandex AI Studio client.
Uses the OpenAI-compatible endpoint of Yandex Foundation Models.
"""
from __future__ import annotations
import json
from typing import Any
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.yandex_ai_api_key,
            base_url=f"{settings.yandex_ai_base_url}/openai/v1",
        )
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def complete(
    system_prompt: str,
    user_message: str,
    response_schema: dict | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """
    Call Yandex GPT with optional structured output.
    Returns parsed dict (structured) or {"text": "..."} (free text).
    """
    client = get_client()

    kwargs: dict = {
        "model": settings.yandex_gpt_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if response_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": response_schema},
        }

    response = await client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""

    if response_schema:
        return json.loads(content)
    return {"text": content, "tokens": response.usage.total_tokens if response.usage else 0}
