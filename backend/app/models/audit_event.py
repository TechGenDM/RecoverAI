import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=True
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_actions.id"), nullable=True
    )

    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(
        String, nullable=False
    )  # system/llm_agent/policy_engine/executor/webhook
    mode: Mapped[str] = mapped_column(String, nullable=False)  # LIVE/SIMULATED
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case = relationship("RecoveryCase", back_populates="audit_events")
    action = relationship("RecoveryAction", back_populates="audit_events")

    __table_args__ = (
        Index("ix_audit_events_case_id_created_at", "case_id", "created_at"),
    )
