from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class ProductTechnology(BaseModel):
    product_type: Literal["Web", "Mobile", "SaaS", "Hardware", "AI"]
    current_features: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    data_strategy: Literal["None", "User Data", "External APIs", "Proprietary"]
    ai_usage: Literal["None", "Planned", "In Production"]
    tech_challenges: str = ""


class MarketingGrowth(BaseModel):
    current_marketing_channels: List[str] = Field(default_factory=list)
    monthly_users: int = 0
    customer_acquisition_cost: str = ""
    retention_strategy: str = ""
    growth_problems: str = ""


class TeamOrganization(BaseModel):
    team_size: int = 0
    founder_roles: List[str] = Field(default_factory=list)
    hiring_plan_next_3_months: str = ""
    org_challenges: str = ""


class CompetitionMarket(BaseModel):
    known_competitors: List[str] = Field(default_factory=list)
    unique_advantage: str = ""
    pricing_model: str = ""
    market_risks: str = ""


class FinanceRunway(BaseModel):
    monthly_burn: str = ""
    current_revenue: str = ""
    funding_status: Literal["Bootstrapped", "Angel", "Seed", "Series A"]
    runway_months: str = ""
    financial_concerns: str = ""


class StartupInput(BaseModel):
    product_technology: ProductTechnology
    marketing_growth: MarketingGrowth
    team_organization: TeamOrganization
    competition_market: CompetitionMarket
    finance_runway: FinanceRunway


# Pydantic models for structured LLM output
class AgentWeaknessOutput(BaseModel):
    """Structured output for agent analysis - weaknesses only"""
    agent_name: str = Field(..., description="Name of the agent providing analysis")
    weaknesses: List[str] = Field(..., description="List of 3-5 specific weaknesses with concrete details", min_items=3, max_items=5)


class WeaknessAnalysisResult(BaseModel):
    """Complete weakness analysis from all agents"""
    marketing_weaknesses: List[str] = Field(default_factory=list)
    tech_weaknesses: List[str] = Field(default_factory=list)
    org_hr_weaknesses: List[str] = Field(default_factory=list)
    competitive_weaknesses: List[str] = Field(default_factory=list)
    finance_weaknesses: List[str] = Field(default_factory=list)


# Legacy models (kept for backward compatibility but not used)
class AgentAnalysis(BaseModel):
    agent_name: str
    strengths: List[str]
    weaknesses: List[str]
    suggestions: List[str]
    next_month_roadmap: List[str]
    detailed_analysis: str


class StartupAnalysisReport(BaseModel):
    marketing_analysis: AgentAnalysis
    tech_analysis: AgentAnalysis
    org_hr_analysis: AgentAnalysis
    competitive_analysis: AgentAnalysis
    finance_analysis: AgentAnalysis
    executive_summary: str
