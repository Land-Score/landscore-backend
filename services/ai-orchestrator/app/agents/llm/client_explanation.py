from app.agents.llm.base_llm import BaseLLMAgent

class ClientExplanationAgent(BaseLLMAgent):
    name = "ClientExplanationAgent"
    prompt_file = "client_explanation_agent.txt"
