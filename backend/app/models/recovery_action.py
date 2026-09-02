import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_decisions.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    action_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # WAIT/SEND_PAYMENT_LINK/ESCALATE/STOP
    status: Mapped[str] = mapped_column(
        String, nullable=False
    )  # PENDING/EXECUTING/SUCCESS/FAILED
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    razorpay_link_id: Mapped[str | None] = mapped_column(String, nullable=True)
    razorpay_link_short_url: Mapped[str | None] = mapped_column(String, nullable=True)
    razorpay_link_reference_id: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    razorpay_link_expire_by: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # RECOVERED/EXPIRED/CANCELLED/FAILED
    outcome_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case = relationship("RecoveryCase", back_populates="actions")
    decision = relationship("RecoveryDecision", back_populates="actions")
    audit_events = relationship("AuditEvent", back_populates="action")

    __table_args__ = (
        Index("ix_recovery_actions_case_id_attempt", "case_id", "attempt_number"),
        Index("ix_recovery_actions_reference_id", "razorpay_link_reference_id"),
    )
