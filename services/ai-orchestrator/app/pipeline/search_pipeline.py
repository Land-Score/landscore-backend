from app.pipeline.base import Agent, ParallelGroup
from app.agents.llm.request_understanding import RequestUnderstandingAgent
from app.agents.llm.search_criteria import SearchCriteriaAgent
from app.agents.code.land_scout import LandScoutAgent
from app.agents.code.candidate_filtering import CandidateFilteringAgent
from app.agents.code.shortlist_ranking import ShortlistRankingAgent
from app.agents.llm.chief_decision import ChiefDecisionAgent
from app.agents.llm.report import ReportAgent
from app.agents.llm.next_steps import NextStepsAgent


def build_search_pipeline() -> list[Agent | ParallelGroup]:
    return [
        RequestUnderstandingAgent(),
        SearchCriteriaAgent(),
        LandScoutAgent(),
        CandidateFilteringAgent(),
        ShortlistRankingAgent(),
        # Deep Check of top N candidates runs check_pipeline per candidate
        ChiefDecisionAgent(),
        ParallelGroup([ReportAgent(), NextStepsAgent()]),
    ]
