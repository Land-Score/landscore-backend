from app.agents.llm.base_llm import BaseLLMAgent

class RestrictionsAgent(BaseLLMAgent):
    name = "RestrictionsAgent"
    prompt_file = "restrictions_agent.txt"
