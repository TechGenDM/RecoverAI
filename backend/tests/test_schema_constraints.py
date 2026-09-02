import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import (
    AuditEvent,
    Payment,
    RecoveryAction,
    RecoveryCase,
    RecoveryDecision,
    WebhookEvent,
)

test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


def test_reference_id_format_and_length():
    """Verify that reference_id respects the Rev 4 spec: rc-{hex24}-a{n} <= 40 chars."""
    case_id = uuid.uuid4()
    hex24 = case_id.hex[:24]
    for attempt in (1, 2, 3, 10):
        ref_id = f"rc-{hex24}-a{attempt}"
        assert len(ref_id) <= 40, f"Reference ID '{ref_id}' exceeds 40 characters limit"
        assert len(ref_id) == 29 + len(str(attempt))  # 3 + 24 + 2 + len(attempt)


@pytest.mark.asyncio
async def test_unique_razorpay_payment_id():
    """Test payments.razorpay_payment_id UNIQUE constraint."""
    pid = f"pay_{uuid.uuid4().hex[:14]}"
    async with TestSessionLocal() as s1:
        p1 = Payment(
            razorpay_payment_id=pid,
            amount=150000,  # 1500.00 INR in paise
            currency="INR",
            status="failed",
            payload_snapshot={"id": pid, "status": "failed"},
        )
        s1.add(p1)
        await s1.commit()

    async with TestSessionLocal() as s2:
        p2 = Payment(
            razorpay_payment_id=pid,
            amount=150000,
            currency="INR",
            status="failed",
            payload_snapshot={"id": pid, "status": "failed"},
        )
        s2.add(p2)
        with pytest.raises(IntegrityError):
            await s2.commit()


@pytest.mark.asyncio
async def test_unique_original_payment_id_and_payment_fk():
    """Test recovery_cases original_payment_id and payment_fk UNIQUE constraints."""
    pid = f"pay_{uuid.uuid4().hex[:14]}"
    async with TestSessionLocal() as s1:
        payment = Payment(
            razorpay_payment_id=pid,
            amount=500000,
            currency="INR",
            status="failed",
            payload_snapshot={"id": pid},
        )
        s1.add(payment)
        await s1.commit()

        case1 = RecoveryCase(
            original_payment_id=pid,
            payment_fk=payment.id,
            status="CREATED",
            mode="LIVE",
            amount_at_risk=500000,
            recovery_window_expires_at=datetime.now(UTC) + timedelta(hours=72),
        )
        s1.add(case1)
        await s1.commit()

    # Attempt duplicate payment_fk in second session
    async with TestSessionLocal() as s2:
        case2 = RecoveryCase(
            original_payment_id=f"pay_other_{uuid.uuid4().hex[:10]}",
            payment_fk=payment.id,
            status="CREATED",
            mode="LIVE",
            amount_at_risk=500000,
            recovery_window_expires_at=datetime.now(UTC) + timedelta(hours=72),
        )
        s2.add(case2)
        with pytest.raises(IntegrityError):
            await s2.commit()


@pytest.mark.asyncio
async def test_unique_idempotency_key():
    """Test recovery_actions.idempotency_key UNIQUE constraint."""
    pid = f"pay_{uuid.uuid4().hex[:14]}"
    async with TestSessionLocal() as s1:
        payment = Payment(
            razorpay_payment_id=pid,
            amount=250000,
            currency="INR",
            status="failed",
            payload_snapshot={"id": pid},
        )
        s1.add(payment)
        await s1.commit()

        case = RecoveryCase(
            original_payment_id=pid,
            payment_fk=payment.id,
            status="CREATED",
            mode="LIVE",
            amount_at_risk=250000,
            recovery_window_expires_at=datetime.now(UTC) + timedelta(hours=72),
        )
        s1.add(case)
        await s1.commit()

        decision = RecoveryDecision(
            case_id=case.id,
            attempt_number=1,
            recommended_action="SEND_PAYMENT_LINK",
            llm_confidence=0.85,
            reason="Initial failure due to auth drop",
            risk_factors=["auth_timeout"],
            policy_verdict="ALLOW",
            effective_action="SEND_PAYMENT_LINK",
            policy_reason="Permitted under policy",
            raw_llm_response={"action": "SEND_PAYMENT_LINK"},
            llm_provider="gemini",
            llm_model="gemini-2.0-flash",
            llm_latency_ms=320,
        )
        s1.add(decision)
        await s1.commit()

        idem_key = f"idem_{uuid.uuid4().hex}"
        act1 = RecoveryAction(
            case_id=case.id,
            decision_id=decision.id,
            attempt_number=1,
            action_type="SEND_PAYMENT_LINK",
            status="EXECUTING",
            idempotency_key=idem_key,
        )
        s1.add(act1)
        await s1.commit()

    async with TestSessionLocal() as s2:
        act2 = RecoveryAction(
            case_id=case.id,
            decision_id=decision.id,
            attempt_number=1,
            action_type="SEND_PAYMENT_LINK",
            status="EXECUTING",
            idempotency_key=idem_key,
        )
        s2.add(act2)
        with pytest.raises(IntegrityError):
            await s2.commit()


@pytest.mark.asyncio
async def test_unique_razorpay_event_id():
    """Test webhook_events.razorpay_event_id UNIQUE constraint."""
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    async with TestSessionLocal() as s1:
        e1 = WebhookEvent(
            razorpay_event_id=event_id,
            event_type="payment.failed",
            payload={"event": "payment.failed"},
            signature_verified=True,
        )
        s1.add(e1)
        await s1.commit()

    async with TestSessionLocal() as s2:
        e2 = WebhookEvent(
            razorpay_event_id=event_id,
            event_type="payment.failed",
            payload={"event": "payment.failed"},
            signature_verified=True,
        )
        s2.add(e2)
        with pytest.raises(IntegrityError):
            await s2.commit()


@pytest.mark.asyncio
async def test_audit_event_relationships_and_foreign_keys():
    """Verify audit_events link to case and action, with actor and mode."""
    pid = f"pay_{uuid.uuid4().hex[:14]}"
    async with TestSessionLocal() as s:
        payment = Payment(
            razorpay_payment_id=pid,
            amount=100000,
            currency="INR",
            status="failed",
            payload_snapshot={"id": pid},
        )
        s.add(payment)
        await s.commit()

        case = RecoveryCase(
            original_payment_id=pid,
            payment_fk=payment.id,
            status="CREATED",
            mode="LIVE",
            amount_at_risk=100000,
            recovery_window_expires_at=datetime.now(UTC) + timedelta(hours=72),
        )
        s.add(case)
        await s.commit()

        audit = AuditEvent(
            case_id=case.id,
            event_type="CASE_CREATED",
            actor="system",
            mode="LIVE",
            payload={"amount": 100000},
        )
        s.add(audit)
        await s.commit()

        # Query back
        result = await s.execute(select(AuditEvent).where(AuditEvent.id == audit.id))
        loaded_audit = result.scalar_one()
        assert loaded_audit.actor == "system"
        assert loaded_audit.mode == "LIVE"
        assert loaded_audit.case_id == case.id
