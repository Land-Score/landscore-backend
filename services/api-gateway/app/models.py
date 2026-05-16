"""Pydantic request/response models for all API Gateway endpoints."""
from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr = Field(examples=["user@example.com"])
    password: str = Field(min_length=8, examples=["strongpass123"])
    name: str = Field(min_length=2, examples=["Иван Иванов"])


class LoginRequest(BaseModel):
    email: EmailStr = Field(examples=["user@example.com"])
    password: str = Field(examples=["strongpass123"])


class RefreshRequest(BaseModel):
    refresh_token: str


class UpdateProfileRequest(BaseModel):
    client_type: str | None = Field(None, examples=["private"],
        description="private | developer | agroholding | investor | legal")
    main_task: str | None = Field(None, examples=["land_check"],
        description="land_check | land_plot_selection | portfolio")
    region: str | None = Field(None, examples=["Московская область"])
    priority: list[str] = Field(default_factory=list, examples=[["legal_risk", "infrastructure"]])
    risk_tolerance: str | None = Field(None, examples=["medium"],
        description="low | medium | high")
    preferred_scenarios: list[str] = Field(default_factory=list, examples=[["construction", "resale"]])
    organization: str | None = Field(None, examples=["ООО Ромашка"])
    budget: float | None = Field(None, examples=[5000000.0])


class UserProfileResponse(BaseModel):
    user_id: str
    email: str
    name: str
    client_type: str
    main_task: str
    region: str
    priority: list[str]
    risk_tolerance: str
    preferred_scenarios: list[str]
    organization: str
    budget: float
    created_at: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    profile: UserProfileResponse


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ── Checks ───────────────────────────────────────────────────────────────────

class CreateCheckRequest(BaseModel):
    cadastral_number: str | None = Field(
        None,
        description="Кадастровый номер в формате XX:XX:XXXXXXX:XX",
        examples=["50:21:0080101:1234"],
    )
    address: str | None = Field(
        None,
        description="Адрес участка (если нет кадастрового номера)",
        examples=["Московская область, Одинцовский район, д. Ромашково"],
    )
    lat: float | None = Field(None, description="Широта (необязательно)", examples=[55.7558])
    lng: float | None = Field(None, description="Долгота (необязательно)", examples=[37.6176])
    purpose: str = Field(
        "",
        description="Цель использования участка",
        examples=["Строительство жилого дома для постоянного проживания"],
    )
    document_ids: list[str] = Field(
        [],
        description="ID ранее загруженных документов (ЕГРН, правоустанавливающие)",
    )


class CheckItemResponse(BaseModel):
    check_id: str
    status: str = Field(description="pending | processing | completed | failed")
    cadastral_number: str | None = None
    address: str | None = None
    purpose: str = ""
    created_at: str
    completed_at: str | None = None


class CheckStatusResponse(BaseModel):
    check_id: str
    status: str
    current_step: str = Field(description="Имя текущего агента")
    progress_pct: int = Field(ge=0, le=100)
    completed_steps: list[dict] = Field(default_factory=list)
    error_message: str = ""


class StopFactor(BaseModel):
    title: str
    description: str


class CheckReportResponse(BaseModel):
    check_id: str
    status: str
    overall_score: int | None = Field(None, ge=0, le=100, description="LandScore 0–100")
    legal_risk: str | None = Field(None, description="low | medium | high | critical")
    stop_factors: list[str] = Field(default_factory=list)
    best_scenario: str | None = None
    report_json: str = Field(description="Полный JSON-отчёт со всеми агентами")
    explanation: str = Field(description="Объяснение по-русски для пользователя")
    next_steps: list[str] = Field(default_factory=list)


class ListChecksResponse(BaseModel):
    checks: list[CheckItemResponse]
    total: int


# ── Searches ─────────────────────────────────────────────────────────────────

class CreateSearchRequest(BaseModel):
    query: str = Field(
        description="Описание задачи в свободной форме",
        examples=["Ищу участок 15–25 соток под ИЖС в Подмосковье, бюджет 3 млн руб, хочу газ"],
        min_length=10,
    )


class SearchItemResponse(BaseModel):
    search_id: str
    status: str = Field(
        description="pending | awaiting_confirmation | processing | completed | failed"
    )
    query: str
    candidates_count: int = 0
    created_at: str


class SearchStatusResponse(BaseModel):
    search_id: str
    status: str
    current_step: str = ""
    progress_pct: int = Field(ge=0, le=100)


class SearchCriteriaResponse(BaseModel):
    search_id: str
    criteria_json: str = Field(
        description="Структурированные критерии поиска (JSON строка)"
    )
    confirmed: bool = Field(description="Подтверждены ли критерии пользователем")


class ConfirmCriteriaRequest(BaseModel):
    criteria_json: str = Field(
        description="Критерии поиска (JSON строка). Можно отредактировать перед подтверждением"
    )


class CandidateResponse(BaseModel):
    plot_id: str = Field(description="Кадастровый номер участка-кандидата")
    rank: int = Field(description="Позиция в итоговом списке (1 = лучший)")
    scores_json: str = Field(description="JSON с детальными оценками по критериям")
    plot_summary_json: str = Field(description="JSON с кратким паспортом участка для карточки")


class SearchResultsResponse(BaseModel):
    candidates: list[CandidateResponse]


class RecommendationResponse(BaseModel):
    search_id: str
    recommendation_json: str
    top_plot_ids: list[str] = Field(description="Топ-3 кадастровых номера")
    explanation: str = Field(description="Объяснение выбора по-русски")


class ListSearchesResponse(BaseModel):
    searches: list[SearchItemResponse]
    total: int


# ── Documents ────────────────────────────────────────────────────────────────

class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    size_bytes: int
    content_type: str


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    size_bytes: int
    download_url: str = Field(description="Presigned URL, действует 24 часа")


# ── Health ───────────────────────────────────────────────────────────────────

class ServiceHealthItem(BaseModel):
    name: str
    address: str
    status: str = Field(description="ok | unreachable")
    latency_ms: int | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str = Field(description="ok | degraded")
    version: str
    services: list[ServiceHealthItem]
