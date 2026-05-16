from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext


class ConstructionScenarioAgent(Agent):
    name = "ConstructionScenarioAgent"
    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True, data={"scenario": "construction", "feasible": True})

class AgricultureScenarioAgent(Agent):
    name = "AgricultureScenarioAgent"
    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True, data={"scenario": "agriculture", "feasible": True})

class LeaseScenarioAgent(Agent):
    name = "LeaseScenarioAgent"
    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True, data={"scenario": "lease", "feasible": True})

class ResaleScenarioAgent(Agent):
    name = "ResaleScenarioAgent"
    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True, data={"scenario": "resale", "feasible": True})

class MixedUseScenarioAgent(Agent):
    name = "MixedUseScenarioAgent"
    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True, data={"scenario": "mixed_use", "feasible": False})
