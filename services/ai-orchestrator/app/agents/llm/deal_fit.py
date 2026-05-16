from app.agents.llm.base_llm import BaseLLMAgent

class DealFitAgent(BaseLLMAgent):
    name = "DealFitAgent"
    prompt_file = "deal_fit_agent.txt"
