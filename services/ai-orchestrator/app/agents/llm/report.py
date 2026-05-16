from app.agents.llm.base_llm import BaseLLMAgent

class ReportAgent(BaseLLMAgent):
    name = "ReportAgent"
    prompt_file = "report_agent.txt"
