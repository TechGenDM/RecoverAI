import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditEvent, RecoveryCase, RecoveryDecision

from .context_builder import build_recovery_context
from .llm import get_llm_provider
from .safety_validator import compute_heuristic_likelihood, validate_decision


async def analyze_and_decide(session: AsyncSession, case: RecoveryCase) -> None:
    """
    Core M2 orchestration logic for a single case.
    Assumes caller holds necessary locks or owns the case.
    Does NOT commit the transaction (caller handles it).
    """
    llm = get_llm_provider()

    # 1. Build deterministic context
    context = await build_recovery_context(session, case)

    # 2. Call LLM
    start_time = time.monotonic()
    decision_schema, raw_response = await llm.analyze_case(context)
    latency_ms = int((time.monotonic() - start_time) * 1000)

    # 3. Safety validation
    safety_result = validate_decision(decision_schema, context)

    # 4. Compute heuristic likelihood deterministically
    heuristic = compute_heuristic_likelihood(context.payment.error_reason)

    # 5. Persist Decision
    decision = RecoveryDecision(
        case_id=case.id,
        attempt_number=case.attempt_count,
        recommended_action=decision_schema.action,
        llm_confidence=decision_schema.llm_confidence,
        delay_hours=decision_schema.delay_hours,
        reason=decision_schema.reason,
        risk_factors=decision_schema.risk_factors,
        heuristic_recovery_likelihood=heuristic,
        raw_llm_response=raw_response,
        llm_provider=settings.LLM_PROVIDER,
        llm_model=settings.LLM_MODEL,
        llm_latency_ms=latency_ms,
        policy_verdict=safety_result.policy_verdict,
        effective_action=safety_result.effective_action,
        policy_reason=safety_result.policy_reason,
        policy_modification_detail=safety_result.policy_modification_detail,
    )
    session.add(decision)

    # 6. Update Case Status based on effective_action
    old_status = case.status
    if safety_result.effective_action == "STOP":
        case.status = "STOPPED"
        case.due_at = None
    elif safety_result.effective_action == "ESCALATE":
        case.status = "ESCALATED"
        case.due_at = None
    elif safety_result.effective_action == "WAIT":
        case.status = "WAITING"
        # Using context evaluation_time string is not ideal for DB datetime insertion directly if naive
        # We'll use SQLAlchemy func.now() or python datetime
        # Import datetime UTC if needed, but we can just use python runtime datetime
        from datetime import UTC, datetime, timedelta

        case.due_at = datetime.now(UTC) + timedelta(
            hours=safety_result.delay_hours or 1.0
        )
    elif safety_result.effective_action == "SEND_PAYMENT_LINK":
        # Case remains in ANALYSING so M3 can pick it up via JOIN on recovery_actions
        case.status = "ANALYSING"
        case.due_at = None

    # 7. Audit Log
    audit = AuditEvent(
        case_id=case.id,
        event_type="LLM_ANALYSIS_COMPLETED",
        actor="system",
        mode=case.mode,
        payload={
            "description": f"Analysis complete. Action: {safety_result.effective_action}",
            "old_status": old_status,
            "new_status": case.status,
            "latency_ms": latency_ms,
            "llm_provider": settings.LLM_PROVIDER,
            "policy_verdict": safety_result.policy_verdict,
        },
    )
    session.add(audit)
