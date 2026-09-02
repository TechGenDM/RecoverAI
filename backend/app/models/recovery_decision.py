import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RecoveryDecision(Base):
    __tablename__ = "recovery_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    recommended_action: Mapped[str] = mapped_column(String, nullable=False)
    llm_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    delay_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    risk_factors: Mapped[list] = mapped_column(JSONB, nullable=False)

    heuristic_recovery_likelihood: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    policy_verdict: Mapped[str] = mapped_column(String, nullable=False)
    effective_action: Mapped[str] = mapped_column(String, nullable=False)
    policy_modification_detail: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    policy_reason: Mapped[str] = mapped_column(String, nullable=False)

    raw_llm_response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    llm_provider: Mapped[str] = mapped_column(String, nullable=False)
    llm_model: Mapped[str] = mapped_column(String, nullable=False)
    llm_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case = relationship("RecoveryCase", back_populates="decisions")
    actions = relationship("RecoveryAction", back_populates="decision")
