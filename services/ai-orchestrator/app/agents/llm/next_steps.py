from app.agents.llm.base_llm import BaseLLMAgent

class NextStepsAgent(BaseLLMAgent):
    name = "NextStepsAgent"
    prompt_file = "next_steps_agent.txt"
