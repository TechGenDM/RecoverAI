import datetime
import uuid

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models import (
    AuditEvent,
    Customer,
    Payment,
    RecoveryAction,
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
async def async_client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
async def clean_database():
    async with TestSessionLocal() as session:
        await session.execute(delete(AuditEvent))
        await session.execute(delete(RecoveryAction))
        await session.execute(delete(RecoveryDecision))
        await session.execute(delete(RecoveryCase))
        await session.execute(delete(Payment))
        await session.execute(delete(Customer))
        await session.commit()
    yield
    async with TestSessionLocal() as session:
        await session.execute(delete(AuditEvent))
        await session.execute(delete(RecoveryAction))
        await session.execute(delete(RecoveryDecision))
        await session.execute(delete(RecoveryCase))
        await session.execute(delete(Payment))
        await session.execute(delete(Customer))
        await session.commit()


@pytest.mark.asyncio
async def test_case_detail_includes_short_url_when_present(db_session, async_client):
    """Verify that get_case_detail includes razorpay_link_short_url when populated."""
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    case_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    action_id = uuid.uuid4()

    customer = Customer(
        id=customer_id,
        email="test@example.com",
        phone="+919876543210",
        name="Test Customer",
    )
    payment = Payment(
        id=payment_id,
        razorpay_payment_id="pay_failed_123",
        customer_id=customer_id,
        amount=50000,
        currency="INR",
        status="failed",
        error_code="BAD_REQUEST_ERROR",
        error_reason="payment_failed",
        method="card",
        created_at=datetime.datetime.now(datetime.UTC),
        payload_snapshot={},
    )
    case = RecoveryCase(
        id=case_id,
        original_payment_id="pay_failed_123",
        payment_fk=payment_id,
        customer_id=customer_id,
        status="LINK_SENT",
        mode="LIVE",
        amount_at_risk=50000,
        attempt_count=1,
        recovery_window_expires_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(hours=24),
    )
    decision = RecoveryDecision(
        id=decision_id,
        case_id=case_id,
        attempt_number=1,
        recommended_action="SEND_PAYMENT_LINK",
        effective_action="SEND_PAYMENT_LINK",
        policy_verdict="APPROVED",
        policy_reason="Approved candidate",
        reason="Good recovery candidate",
        risk_factors=["LOW_RISK"],
        llm_confidence=0.9,
        raw_llm_response={"action": "SEND_PAYMENT_LINK"},
        llm_provider="test",
        llm_model="test",
        llm_latency_ms=10,
    )
    action = RecoveryAction(
        id=action_id,
        case_id=case_id,
        decision_id=decision_id,
        attempt_number=1,
        action_type="SEND_PAYMENT_LINK",
        status="SUCCESS",
        idempotency_key=f"{case_id}-1-exec",
        razorpay_link_id="plink_test_12345",
        razorpay_link_reference_id=f"rc-{case_id.hex[:24]}-a1",
        razorpay_link_short_url="https://rzp.io/i/test_short_url",
        executed_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add_all([customer, payment, case, decision, action])
    await db_session.commit()

    resp = await async_client.get(f"/v1/cases/{case_id}")
    assert resp.status_code == 200
    data = resp.json()

    assert "actions" in data
    assert len(data["actions"]) == 1
    action_data = data["actions"][0]
    assert action_data["razorpay_link_id"] == "plink_test_12345"
    assert action_data["razorpay_link_short_url"] == "https://rzp.io/i/test_short_url"


@pytest.mark.asyncio
async def test_case_detail_short_url_null_when_unavailable(db_session, async_client):
    """Verify that razorpay_link_short_url is null when not yet generated or failed."""
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    case_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    action_id = uuid.uuid4()

    customer = Customer(
        id=customer_id,
        email="test@example.com",
        phone="+919876543210",
        name="Test Customer",
    )
    payment = Payment(
        id=payment_id,
        razorpay_payment_id="pay_failed_456",
        customer_id=customer_id,
        amount=25000,
        currency="INR",
        status="failed",
        error_code="BAD_REQUEST_ERROR",
        error_reason="payment_failed",
        method="upi",
        created_at=datetime.datetime.now(datetime.UTC),
        payload_snapshot={},
    )
    case = RecoveryCase(
        id=case_id,
        original_payment_id="pay_failed_456",
        payment_fk=payment_id,
        customer_id=customer_id,
        status="EXECUTING",
        mode="LIVE",
        amount_at_risk=25000,
        attempt_count=1,
        recovery_window_expires_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(hours=24),
    )
    decision = RecoveryDecision(
        id=decision_id,
        case_id=case_id,
        attempt_number=1,
        recommended_action="SEND_PAYMENT_LINK",
        effective_action="SEND_PAYMENT_LINK",
        policy_verdict="APPROVED",
        policy_reason="Approved candidate",
        reason="Good candidate",
        risk_factors=["LOW_RISK"],
        llm_confidence=0.8,
        raw_llm_response={"action": "SEND_PAYMENT_LINK"},
        llm_provider="test",
        llm_model="test",
        llm_latency_ms=10,
    )
    action = RecoveryAction(
        id=action_id,
        case_id=case_id,
        decision_id=decision_id,
        attempt_number=1,
        action_type="SEND_PAYMENT_LINK",
        status="PENDING",
        idempotency_key=f"{case_id}-1-exec",
        razorpay_link_id=None,
        razorpay_link_reference_id=f"rc-{case_id.hex[:24]}-a1",
        razorpay_link_short_url=None,
    )
    db_session.add_all([customer, payment, case, decision, action])
    await db_session.commit()

    resp = await async_client.get(f"/v1/cases/{case_id}")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["actions"]) == 1
    action_data = data["actions"][0]
    assert action_data["razorpay_link_short_url"] is None
    assert action_data["razorpay_link_id"] is None


@pytest.mark.asyncio
async def test_case_detail_pii_sanitization_intact(db_session, async_client):
    """Verify that case detail preserves strict PII masking and does not leak credentials or raw LLM output."""
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    case_id = uuid.uuid4()
    decision_id = uuid.uuid4()

    customer = Customer(
        id=customer_id,
        email="sensitive_customer@example.com",
        phone="+919876543210",
        name="Sensitive Customer",
    )
    payment = Payment(
        id=payment_id,
        razorpay_payment_id="pay_failed_789",
        customer_id=customer_id,
        amount=10000,
        currency="INR",
        status="failed",
        error_code="GATEWAY_ERROR",
        error_reason="gateway_error",
        method="card",
        created_at=datetime.datetime.now(datetime.UTC),
        payload_snapshot={
            "raw_card_number": "4111222233334444",
            "secret_cvv": "123",
            "internal_token": "tok_sec_9999",
        },
    )
    case = RecoveryCase(
        id=case_id,
        original_payment_id="pay_failed_789",
        payment_fk=payment_id,
        customer_id=customer_id,
        status="ANALYSING",
        mode="LIVE",
        amount_at_risk=10000,
        attempt_count=1,
        recovery_window_expires_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(hours=24),
    )
    decision = RecoveryDecision(
        id=decision_id,
        case_id=case_id,
        attempt_number=1,
        recommended_action="SEND_PAYMENT_LINK",
        effective_action="SEND_PAYMENT_LINK",
        policy_verdict="APPROVED",
        policy_reason="Policy approved",
        reason="Good recovery candidate",
        risk_factors=["LOW_RISK"],
        llm_confidence=0.85,
        raw_llm_response={
            "sensitive_token": "SENSITIVE_INTERNAL_LLM_PROMPT_AND_OUTPUT"
        },
        llm_provider="google",
        llm_model="gemini-2.0-flash",
        llm_latency_ms=120,
    )
    db_session.add_all([customer, payment, case, decision])
    await db_session.commit()

    resp = await async_client.get(f"/v1/cases/{case_id}")
    assert resp.status_code == 200
    data = resp.json()

    # Customer capability booleans only
    assert data["customer_capability"] == {"has_email": True, "has_phone": True}
    raw_response_text = resp.text

    # Verify no raw PII leaks in response
    assert "sensitive_customer@example.com" not in raw_response_text
    assert "+919876543210" not in raw_response_text
    assert "Sensitive Customer" not in raw_response_text

    # Verify no raw payload secrets or card numbers leaked
    assert "4111222233334444" not in raw_response_text
    assert "tok_sec_9999" not in raw_response_text
    assert "payload_snapshot" not in raw_response_text

    # Verify no raw LLM response leaked
    assert "SENSITIVE_INTERNAL_LLM_PROMPT_AND_OUTPUT" not in raw_response_text
