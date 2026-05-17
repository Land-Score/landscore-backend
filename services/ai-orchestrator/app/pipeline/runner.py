from __future__ import annotations
import asyncio
import time
from typing import Callable, Awaitable

from app.pipeline.base import Agent, AgentResult, ParallelGroup
from app.pipeline.context import AgentContext


ProgressCallback = Callable[[str, int, AgentResult], Awaitable[None]]


class PipelineRunner:
    """
    Executes a pipeline of agents sequentially,
    with optional parallel groups. Reports progress after each step.
    """

    def __init__(
        self,
        pipeline: list[Agent | ParallelGroup],
        on_progress: ProgressCallback | None = None,
    ):
        self.pipeline = pipeline
        self.on_progress = on_progress

    async def run(self, ctx: AgentContext) -> dict:
        total = len(self.pipeline)
        results: dict = {}

        for idx, step in enumerate(self.pipeline):
            pct = int(((idx + 1) / total) * 100)

            if isinstance(step, ParallelGroup):
                group_results = await step.run(ctx)
                for agent, result in zip(step.agents, group_results):
                    if result.success:
                        ctx.set(agent.name, result.data)
                        results[agent.name] = result
                    else:
                        # Log but don't abort — partial results are acceptable
                        print(f"[WARN] Agent {agent.name} failed: {result.error}")
                    if self.on_progress:
                        await self.on_progress(agent.name, pct, result)
            else:
                start = time.monotonic()
                result = await step.run({}, ctx)
                result.duration_ms = result.duration_ms or int((time.monotonic() - start) * 1000)

                if result.success:
                    ctx.set(step.name, result.data)
                    results[step.name] = result
                else:
                    print(f"[ERROR] Agent {step.name} failed: {result.error}")

                if self.on_progress:
                    await self.on_progress(step.name, pct, result)

        return results
