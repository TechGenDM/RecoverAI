import pytest
from sqlalchemy import select

from app.models import AuditEvent, RecoveryCase, RecoveryDecision
from app.services.analysis_service import analyze_and_decide


@pytest.mark.asyncio
async def test_analyze_and_decide(db_session, setup_test_data):
    # 1. Get a case
    case = (
        await db_session.execute(
            select(RecoveryCase).where(RecoveryCase.status == "CREATED").limit(1)
        )
    ).scalar_one()

    old_status = case.status

    # 2. Run analysis
    await analyze_and_decide(db_session, case)

    # We must commit to verify persistence, or just read from session
    await db_session.commit()

    # 3. Check Case Status
    assert case.status in ["ANALYSING", "WAITING", "STOPPED", "ESCALATED"]

    # 4. Check Decision Persisted
    decision = (
        await db_session.execute(
            select(RecoveryDecision).where(RecoveryDecision.case_id == case.id)
        )
    ).scalar_one()

    assert decision.recommended_action in [
        "WAIT",
        "SEND_PAYMENT_LINK",
        "ESCALATE",
        "STOP",
    ]
    assert decision.llm_provider is not None
    assert decision.policy_verdict in ["ALLOW", "MODIFY", "DENY"]
    assert (
        decision.effective_action == case.status
        if case.status != "ANALYSING"
        else "SEND_PAYMENT_LINK"
    )

    # 5. Check Audit Event
    audit = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.case_id == case.id,
                AuditEvent.event_type == "LLM_ANALYSIS_COMPLETED",
            )
        )
    ).scalar_one_or_none()

    assert audit is not None
    assert audit.payload["old_status"] == old_status
    assert audit.payload["new_status"] == case.status
