from __future__ import annotations
import time
from pathlib import Path
from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app import yandex_ai


PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class BaseLLMAgent(Agent):
    """Base class for all LLM-powered agents."""

    prompt_file: str = ""
    response_schema: dict | None = None
    temperature: float = 0.2

    def _load_prompt(self) -> str:
        if self.prompt_file:
            path = PROMPTS_DIR / self.prompt_file
            if path.exists():
                return path.read_text(encoding="utf-8")
        return f"You are the {self.name} for a land plot analysis system."

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        start = time.monotonic()
        try:
            system_prompt = self._load_prompt()
            user_message = self._build_user_message(input, ctx)

            result = await yandex_ai.complete(
                system_prompt=system_prompt,
                user_message=user_message,
                response_schema=self.response_schema,
                temperature=self.temperature,
            )

            elapsed = int((time.monotonic() - start) * 1000)
            return AgentResult(success=True, data=result, duration_ms=elapsed)

        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return AgentResult(success=False, data={}, error=str(e), duration_ms=elapsed)

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        """Override in subclass to build the specific user message."""
        import json
        return json.dumps({
            "profile": ctx.profile.__dict__,
            "plot": ctx.plot.__dict__,
            "input": input,
        }, ensure_ascii=False)
