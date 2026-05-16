import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Integer, JSON, Boolean, ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID


class Base(DeclarativeBase):
    pass


class LandSearch(Base):
    __tablename__ = "land_searches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    query: Mapped[str] = mapped_column(String, default="")
    user_profile_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SearchCriteria(Base):
    __tablename__ = "search_criteria"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    search_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, unique=True)
    criteria_json: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SearchStep(Base):
    __tablename__ = "search_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    search_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SearchCandidate(Base):
    __tablename__ = "search_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    search_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    plot_id: Mapped[str] = mapped_column(String(50))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    scores_json: Mapped[dict] = mapped_column(JSON, default=dict)
    plot_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SearchRecommendation(Base):
    __tablename__ = "search_recommendations"

    search_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    recommendation_json: Mapped[dict] = mapped_column(JSON, default=dict)
    top_plot_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    explanation: Mapped[str] = mapped_column(String, default="")
