from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext

class InfrastructureAgent(Agent):
    name = "InfrastructureAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        # TODO: implement
        return AgentResult(success=True, data={})
