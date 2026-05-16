from app.agents.llm.base_llm import BaseLLMAgent

class CriticalRiskAgent(BaseLLMAgent):
    name = "CriticalRiskAgent"
    prompt_file = "critical_risk_agent.txt"
