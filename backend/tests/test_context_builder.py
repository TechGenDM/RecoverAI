import pytest

from app.models import RecoveryCase
from app.services.context_builder import build_recovery_context


@pytest.mark.asyncio
async def test_build_recovery_context(db_session, setup_test_data):
    # Retrieve the case from DB
    from sqlalchemy import select

    case = (
        await db_session.execute(
            select(RecoveryCase).where(RecoveryCase.status == "CREATED").limit(1)
        )
    ).scalar_one()

    context = await build_recovery_context(db_session, case)

    assert context.case_id == str(case.id)
    assert context.payment.payment_id == case.payment.razorpay_payment_id
    assert context.customer.has_email is True
    assert context.historical.failed_payments >= 0
    assert context.recovery_history.attempt_count == 0
    assert context.time.hours_since_failure > 0
    assert context.can_generate_payment_link is True
