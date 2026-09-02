import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    razorpay_payment_id: Mapped[str] = mapped_column(
        String, unique=True, nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String, nullable=False)

    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_description: Mapped[str | None] = mapped_column(String, nullable=True)
    error_source: Mapped[str | None] = mapped_column(String, nullable=True)
    error_step: Mapped[str | None] = mapped_column(String, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    method: Mapped[str | None] = mapped_column(String, nullable=True)
    bank: Mapped[str | None] = mapped_column(String, nullable=True)
    card_network: Mapped[str | None] = mapped_column(String, nullable=True)
    vpa: Mapped[str | None] = mapped_column(String, nullable=True)
    international: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_id: Mapped[str | None] = mapped_column(String, nullable=True)

    payload_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    customer = relationship("Customer", back_populates="payments")
    recovery_case = relationship(
        "RecoveryCase", back_populates="payment", uselist=False
    )

    __table_args__ = (Index("ix_payments_status_failed_at", "status", "failed_at"),)
