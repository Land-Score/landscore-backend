from app.pipeline.base import Agent, ParallelGroup
from app.agents.llm.request_understanding import RequestUnderstandingAgent
from app.agents.llm.object_identification import ObjectIdentificationAgent
from app.agents.code.data_request import DataRequestAgent
from app.agents.code.search_planning import SearchPlanningAgent
from app.agents.llm.document_extraction import DocumentExtractionAgent
from app.agents.code.fact_normalization import FactNormalizationAgent
from app.agents.llm.legal import LegalAgent
from app.agents.llm.land_use import LandUseAgent
from app.agents.llm.restrictions import RestrictionsAgent
from app.agents.code.infrastructure import InfrastructureAgent
from app.agents.code.geo import GeoAgent
from app.agents.code.market import MarketAgent
from app.agents.llm.critical_risk import CriticalRiskAgent
from app.agents.code.scenario_selector import ScenarioSelectorAgent
from app.agents.code.scenario_agents import (
    ConstructionScenarioAgent, AgricultureScenarioAgent,
    LeaseScenarioAgent, ResaleScenarioAgent, MixedUseScenarioAgent,
)
from app.agents.code.profitability_calculator import ProfitabilityCalculatorAgent
from app.agents.code.scenario_ranking import ScenarioRankingAgent
from app.agents.llm.chief_decision import ChiefDecisionAgent
from app.agents.llm.deal_fit import DealFitAgent
from app.agents.llm.report import ReportAgent
from app.agents.llm.client_explanation import ClientExplanationAgent
from app.agents.llm.next_steps import NextStepsAgent


def build_check_pipeline() -> list[Agent | ParallelGroup]:
    return [
        RequestUnderstandingAgent(),
        ObjectIdentificationAgent(),
        DataRequestAgent(),
        SearchPlanningAgent(),
        DocumentExtractionAgent(),
        FactNormalizationAgent(),
        ParallelGroup([
            LegalAgent(),
            LandUseAgent(),
            RestrictionsAgent(),
            InfrastructureAgent(),
            GeoAgent(),
            MarketAgent(),
        ]),
        CriticalRiskAgent(),
        ScenarioSelectorAgent(),
        ParallelGroup([
            ConstructionScenarioAgent(),
            AgricultureScenarioAgent(),
            LeaseScenarioAgent(),
            ResaleScenarioAgent(),
            MixedUseScenarioAgent(),
        ]),
        ProfitabilityCalculatorAgent(),
        ScenarioRankingAgent(),
        ChiefDecisionAgent(),
        DealFitAgent(),
        ParallelGroup([
            ReportAgent(),
            ClientExplanationAgent(),
            NextStepsAgent(),
        ]),
    ]
