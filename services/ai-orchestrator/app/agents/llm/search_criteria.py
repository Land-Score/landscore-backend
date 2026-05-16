from app.agents.llm.base_llm import BaseLLMAgent

class SearchCriteriaAgent(BaseLLMAgent):
    name = "SearchCriteriaAgent"
    prompt_file = "search_criteria_agent.txt"
