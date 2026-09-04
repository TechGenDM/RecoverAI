import datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import (
    AuditEvent,
    Customer,
    Payment,
    RecoveryCase,
    RecoveryDecision,
)

test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture
async def db_session():
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def setup_test_data(db_session):
    # Setup standard test data for M2
    import uuid

    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    case_id = uuid.uuid4()

    from app.models import RecoveryAction

    # ensure clean slate
    await db_session.execute(delete(AuditEvent))
    await db_session.execute(delete(RecoveryAction))
    await db_session.execute(delete(RecoveryDecision))
    await db_session.execute(delete(RecoveryCase))
    await db_session.execute(delete(Payment))
    await db_session.execute(delete(Customer))

    customer = Customer(
        id=customer_id,
        email="test@example.com",
        phone="+1234567890",
        name="Test Customer",
    )
    db_session.add(customer)

    payment = Payment(
        id=payment_id,
        razorpay_payment_id="pay_" + str(uuid.uuid4())[:10],
        customer_id=customer_id,
        amount=1000,
        currency="INR",
        status="failed",
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment failed",
        error_reason="insufficient_funds",
        method="card",
        created_at=datetime.datetime.now(datetime.UTC),
        payload_snapshot={"test": "data"},
    )
    db_session.add(payment)

    case = RecoveryCase(
        id=case_id,
        original_payment_id=payment.razorpay_payment_id,
        payment_fk=payment_id,
        customer_id=customer_id,
        status="CREATED",
        mode="test",
        amount_at_risk=1000,
        recovery_window_expires_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(hours=24),
    )
    db_session.add(case)

    await db_session.commit()
    yield

    # Teardown
    await db_session.execute(delete(AuditEvent))
    await db_session.execute(delete(RecoveryAction))
    await db_session.execute(delete(RecoveryDecision))
    await db_session.execute(delete(RecoveryCase))
    await db_session.execute(delete(Payment))
    await db_session.execute(delete(Customer))
    await db_session.commit()
