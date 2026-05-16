import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ARRAY, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    client_type: Mapped[str] = mapped_column(String(50), default="private")
    main_task: Mapped[str] = mapped_column(String(50), default="land_check")
    region: Mapped[str] = mapped_column(String(255), default="")
    priority: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    risk_tolerance: Mapped[str] = mapped_column(String(20), default="medium")
    preferred_scenarios: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    organization: Mapped[str] = mapped_column(String(255), default="")
    budget: Mapped[float] = mapped_column(Float, default=0.0)
