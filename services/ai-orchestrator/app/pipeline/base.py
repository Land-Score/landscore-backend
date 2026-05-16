from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from dataclasses import dataclass

from app.pipeline.context import AgentContext

T_in = TypeVar("T_in")
T_out = TypeVar("T_out")


@dataclass
class AgentResult:
    success: bool
    data: dict
    error: str | None = None
    tokens_used: int = 0
    duration_ms: int = 0


class Agent(ABC, Generic[T_in, T_out]):
    """Base class for all pipeline agents."""

    name: str = "base"

    @abstractmethod
    async def run(self, input: T_in, ctx: AgentContext) -> AgentResult:
        ...

    def __repr__(self) -> str:
        return f"<Agent: {self.name}>"


class ParallelGroup:
    """Runs a group of agents concurrently via asyncio.gather."""

    def __init__(self, agents: list[Agent]):
        self.agents = agents
        self.name = f"parallel({', '.join(a.name for a in agents)})"

    async def run(self, ctx: AgentContext) -> list[AgentResult]:
        import asyncio
        tasks = [agent.run({}, ctx) for agent in self.agents]
        return await asyncio.gather(*tasks, return_exceptions=False)
