from app.agents.llm.base_llm import BaseLLMAgent

class ObjectIdentificationAgent(BaseLLMAgent):
    name = "ObjectIdentificationAgent"
    prompt_file = "object_identification.txt"
