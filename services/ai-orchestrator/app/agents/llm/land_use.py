from app.agents.llm.base_llm import BaseLLMAgent

class LandUseAgent(BaseLLMAgent):
    name = "LandUseAgent"
    prompt_file = "land_use_agent.txt"
