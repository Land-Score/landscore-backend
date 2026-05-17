from app.agents.llm.base_llm import BaseLLMAgent

class RequestUnderstandingAgent(BaseLLMAgent):
    name = "RequestUnderstandingAgent"
    prompt_file = "request_understanding.txt"
