"""
Yandex AI Studio client.
Uses the OpenAI-compatible endpoint of Yandex Foundation Models.
"""
from __future__ import annotations
import json
from typing import Any
from openai import AsyncOpenAI, BadRequestError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings


_client: AsyncOpenAI | None = None


def _normalize_base_url(value: str) -> str:
    base_url = (value or "https://ai.api.cloud.yandex.net/v1").rstrip("/")
    legacy_suffix = "/foundationModels/v1"
    if base_url.endswith(legacy_suffix):
        return "https://ai.api.cloud.yandex.net/v1"
    return base_url


def _normalize_model(value: str) -> str:
    model = (value or "yandexgpt/latest").strip()
    if model.startswith(("gpt://", "emb://")):
        return model
    if not settings.yandex_ai_folder_id:
        return model
    return f"gpt://{settings.yandex_ai_folder_id}/{model}"


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.yandex_ai_api_key,
            base_url=_normalize_base_url(settings.yandex_ai_base_url),
            default_headers={
                "x-folder-id": settings.yandex_ai_folder_id,
            } if settings.yandex_ai_folder_id else None,
        )
    return _client


def _json_instruction(schema: dict) -> str:
    return (
        "\n\nВерни только валидный JSON-объект. Не оборачивай ответ в markdown. "
        "Все текстовые значения внутри JSON пиши на русском языке. "
        "JSON должен соответствовать этой схеме:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {"text": content}
        try:
            parsed = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return {"text": content}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


async def _create_completion(kwargs: dict[str, Any]):
    return await get_client().chat.completions.create(**kwargs)


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
    kwargs: dict = {
        "model": _normalize_model(settings.yandex_gpt_model),
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

    try:
        response = await _create_completion(kwargs)
    except BadRequestError:
        if not response_schema:
            raise
        fallback_kwargs = dict(kwargs)
        fallback_kwargs.pop("response_format", None)
        fallback_kwargs["messages"] = [
            {"role": "system", "content": system_prompt + _json_instruction(response_schema)},
            {"role": "user", "content": user_message},
        ]
        response = await _create_completion(fallback_kwargs)

    content = response.choices[0].message.content or ""

    if response_schema:
        return _parse_json_object(content)
    return {"text": content, "tokens": response.usage.total_tokens if response.usage else 0}
