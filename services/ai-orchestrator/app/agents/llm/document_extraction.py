from app.agents.llm.base_llm import BaseLLMAgent

class DocumentExtractionAgent(BaseLLMAgent):
    name = "DocumentExtractionAgent"
    prompt_file = "document_extraction.txt"
