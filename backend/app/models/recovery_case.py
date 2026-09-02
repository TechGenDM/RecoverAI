import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    original_payment_id: Mapped[str] = mapped_column(
        String, unique=True, nullable=False
    )
    payment_fk: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), unique=True, nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_at_risk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_recovered: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    recovered_payment_id: Mapped[str | None] = mapped_column(String, nullable=True)

    recovery_window_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    payment = relationship("Payment", back_populates="recovery_case")
    customer = relationship("Customer", back_populates="recovery_cases")
    decisions = relationship(
        "RecoveryDecision", back_populates="case", cascade="all, delete-orphan"
    )
    actions = relationship(
        "RecoveryAction", back_populates="case", cascade="all, delete-orphan"
    )
    audit_events = relationship(
        "AuditEvent", back_populates="case", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_recovery_cases_status_due_at", "status", "due_at"),
        Index(
            "ix_recovery_cases_status_window_expires",
            "status",
            "recovery_window_expires_at",
        ),
        Index("ix_recovery_cases_mode_status", "mode", "status"),
    )
