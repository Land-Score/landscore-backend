from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.report_context import report_context_json
from app.pipeline.context import AgentContext

class ReportAgent(BaseLLMAgent):
    name = "ReportAgent"
    prompt_file = "report.txt"

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        return report_context_json(ctx)
