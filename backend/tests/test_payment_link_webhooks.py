"""M3 Webhook Tests.

Tests 19-30 from the Rev 4 test plan:
- payment_link.paid / expired / cancelled handlers
- exact amount matching
- payment window eligibility
"""

import datetime
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditEvent,
    Customer,
    Payment,
    RecoveryCase,
    RecoveryDecision,
    WebhookEvent,
)
from app.models.recovery_action import RecoveryAction
from app.services.webhook_service import WebhookIngestionService

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────


async def _clean_db(session: AsyncSession) -> None:
    """Clean all tables in correct FK order."""
    await session.execute(delete(WebhookEvent))
    await session.execute(delete(AuditEvent))
    await session.execute(delete(RecoveryAction))
    await session.execute(delete(RecoveryDecision))
    await session.execute(delete(RecoveryCase))
    await session.execute(delete(Payment))
    await session.execute(delete(Customer))
    await session.commit()


async def _create_test_case_and_action(
    session: AsyncSession,
    *,
    case_status: str = "LINK_SENT",
    amount: int = 10000,
    window_hours: int = 24,
) -> tuple[RecoveryCase, RecoveryAction]:
    """Create test customer, payment, case, and action for webhook tests."""
    customer = Customer(email="test@example.com")
    session.add(customer)
    await session.flush()

    payment = Payment(
        razorpay_payment_id=f"pay_{uuid.uuid4().hex[:12]}",
        customer_id=customer.id,
        amount=amount,
        currency="INR",
        status="failed",
        payload_snapshot={},
    )
    session.add(payment)
    await session.flush()

    case = RecoveryCase(
        original_payment_id=payment.razorpay_payment_id,
        payment_fk=payment.id,
        customer_id=customer.id,
        status=case_status,
        mode="SIMULATED",
        attempt_count=1,
        amount_at_risk=amount,
        recovery_window_expires_at=(
            datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=window_hours)
        ),
    )
    session.add(case)
    await session.flush()

    decision = RecoveryDecision(
        case_id=case.id,
        attempt_number=1,
        recommended_action="SEND_PAYMENT_LINK",
        llm_confidence=1.0,
        reason="test",
        risk_factors=[],
        policy_verdict="APPROVED",
        effective_action="SEND_PAYMENT_LINK",
        policy_reason="test",
        raw_llm_response={},
        llm_provider="test",
        llm_model="test",
        llm_latency_ms=10,
    )
    session.add(decision)
    await session.flush()

    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=1,
        action_type="SEND_PAYMENT_LINK",
        status="SUCCESS",
        idempotency_key=f"{case.id}-1-exec",
        razorpay_link_id="plink_test123",
        razorpay_link_reference_id=f"rc-{case.id.hex[:24]}-a1",
    )
    session.add(action)
    await session.flush()
    return case, action


def _build_paid_payload(
    reference_id: str,
    amount: int,
    created_at_ts: int,
    payment_id: str = "pay_new123",
) -> dict:
    """Build a synthetic payment_link.paid webhook payload."""
    return {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_test123",
                    "reference_id": reference_id,
                    "status": "paid",
                    "amount": amount,
                    "currency": "INR",
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": amount,
                    "currency": "INR",
                    "status": "captured",
                    "created_at": created_at_ts,
                }
            },
        },
    }


def _build_terminal_payload(
    reference_id: str, event_type: str = "payment_link.expired"
) -> dict:
    """Build a synthetic payment_link.expired/cancelled payload."""
    status = "expired" if "expired" in event_type else "cancelled"
    return {
        "event": event_type,
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_test123",
                    "reference_id": reference_id,
                    "status": status,
                    "amount": 10000,
                    "currency": "INR",
                }
            }
        },
    }


# ── Tests ────────────────────────────────────────────────────────────


async def test_valid_paid_webhook_recovers(db_session: AsyncSession) -> None:
    """Test 19: Valid paid webhook → case RECOVERED, new Payment created."""
    await _clean_db(db_session)
    case, action = await _create_test_case_and_action(db_session)
    await db_session.commit()

    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    payload = _build_paid_payload(action.razorpay_link_reference_id, 10000, now_ts)

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_test123", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "recovered"

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "RECOVERED"
        assert refreshed_case.recovered_payment_id == "pay_new123"
        assert refreshed_case.amount_recovered == 10000

        # Verify a new Payment record was created
        payments = (
            (
                await db_session.execute(
                    select(Payment).where(Payment.razorpay_payment_id == "pay_new123")
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        new_payment = payments[0]
        assert new_payment.amount == 10000
        assert new_payment.status == "captured"


async def test_duplicate_paid_webhook_idempotent(db_session: AsyncSession) -> None:
    """Test 20: Same event_id processed twice is idempotent."""
    await _clean_db(db_session)
    _case, action = await _create_test_case_and_action(db_session)
    await db_session.commit()

    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    payload = _build_paid_payload(action.razorpay_link_reference_id, 10000, now_ts)

    status_code1, _ = await WebhookIngestionService.process_webhook(
        "ev_dup", payload, db_session
    )
    assert status_code1 == 200

    # Process EXACT SAME event_id again
    status_code2, response2 = await WebhookIngestionService.process_webhook(
        "ev_dup", payload, db_session
    )
    assert status_code2 == 200
    assert response2["status"] == "duplicate_ignored"

    # Only one new payment should exist
    async with db_session.begin():
        payments = (
            (
                await db_session.execute(
                    select(Payment).where(Payment.razorpay_payment_id == "pay_new123")
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1


async def test_unknown_reference_id_ignored(db_session: AsyncSession) -> None:
    """Test 22: Unknown reference_id is safely ignored (200 OK)."""
    await _clean_db(db_session)
    _case, _action = await _create_test_case_and_action(db_session)
    await db_session.commit()

    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    payload = _build_paid_payload("rc-unknown-123", 10000, now_ts)

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_unknown", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "ignored"
    assert response["reason"] == "unknown_reference_id"


async def test_in_window_late_webhook_recovers(db_session: AsyncSession) -> None:
    """Test 23: STOPPED case + in-window payment → RECOVERED."""
    await _clean_db(db_session)
    case, action = await _create_test_case_and_action(db_session, case_status="STOPPED")
    await db_session.commit()

    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    payload = _build_paid_payload(action.razorpay_link_reference_id, 10000, now_ts)

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_late", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "recovered"

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "RECOVERED"


async def test_out_of_window_payment_not_recovered(db_session: AsyncSession) -> None:
    """Test 24: Payment occurred after window expiry → NOT recovered."""
    await _clean_db(db_session)
    case, action = await _create_test_case_and_action(db_session)

    # Set window to expire yesterday
    yesterday = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    case.recovery_window_expires_at = yesterday
    await db_session.commit()

    # Payment happened today (out of window)
    today_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    payload = _build_paid_payload(action.razorpay_link_reference_id, 10000, today_ts)

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_outofwindow", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "out_of_window"

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "LINK_SENT"  # Not recovered


async def test_exact_amount_recovery(db_session: AsyncSession) -> None:
    """Test 27: amount == amount_at_risk → RECOVERED."""
    await _clean_db(db_session)
    _case, action = await _create_test_case_and_action(db_session, amount=5000)
    await db_session.commit()

    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    payload = _build_paid_payload(action.razorpay_link_reference_id, 5000, now_ts)

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_exact", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "recovered"


async def test_mismatched_amount_not_recovered(db_session: AsyncSession) -> None:
    """Test 28: amount ≠ expected → STOPPED + audit anomaly."""
    await _clean_db(db_session)
    case, action = await _create_test_case_and_action(db_session, amount=10000)
    await db_session.commit()

    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    # Webhook claims 5000 paid, but case expects 10000
    payload = _build_paid_payload(action.razorpay_link_reference_id, 5000, now_ts)

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_mismatch", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "amount_mismatch"

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "STOPPED"
        assert "Amount mismatch" in str(refreshed_case.stop_reason)

        # But the payment should STILL be recorded for traceability
        payments = (
            (
                await db_session.execute(
                    select(Payment).where(Payment.razorpay_payment_id == "pay_new123")
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        assert payments[0].amount == 5000


async def test_expired_link_webhook(db_session: AsyncSession) -> None:
    """Test 29: payment_link.expired → STOPPED."""
    await _clean_db(db_session)
    case, action = await _create_test_case_and_action(db_session)
    await db_session.commit()

    payload = _build_terminal_payload(
        action.razorpay_link_reference_id, "payment_link.expired"
    )

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_expired", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "stopped"
    assert response["outcome"] == "EXPIRED"

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "STOPPED"
        assert "expired" in str(refreshed_case.stop_reason)


async def test_cancelled_link_webhook(db_session: AsyncSession) -> None:
    """Test 30: payment_link.cancelled → STOPPED."""
    await _clean_db(db_session)
    case, action = await _create_test_case_and_action(db_session)
    await db_session.commit()

    payload = _build_terminal_payload(
        action.razorpay_link_reference_id, "payment_link.cancelled"
    )

    status_code, response = await WebhookIngestionService.process_webhook(
        "ev_cancelled", payload, db_session
    )
    assert status_code == 200
    assert response["status"] == "stopped"
    assert response["outcome"] == "CANCELLED"

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "STOPPED"
        assert "cancelled" in str(refreshed_case.stop_reason)
