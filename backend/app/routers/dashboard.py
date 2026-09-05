"""Dashboard and case observability API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (
    AuditEvent,
    Customer,
    Payment,
    RecoveryAction,
    RecoveryCase,
    RecoveryDecision,
)
from app.schemas.dashboard import DashboardMetrics
from app.services.metrics_service import get_dashboard_metrics
from app.services.simulation_service import run_simulation

router = APIRouter(prefix="/v1", tags=["Dashboard"])

# --- Valid enum values for filter validation ---
VALID_MODES = {"LIVE", "SIMULATED"}
VALID_STATUSES = {
    "CREATED",
    "ANALYSING",
    "WAITING",
    "EXECUTING",
    "LINK_SENT",
    "RECOVERED",
    "STOPPED",
    "ESCALATED",
}

# --- Safe keys for timeline payload whitelist ---
SAFE_TIMELINE_KEYS = {
    "description",
    "old_status",
    "new_status",
    "latency_ms",
    "llm_provider",
    "policy_verdict",
    "reference_id",
    "idempotency_key",
    "attempt_number",
    "reason",
    "razorpay_link_id",
    "short_url",
    "recovered_payment_id",
    "error",
    "match_count",
    "min_validity_minutes",
    "current_status",
    "result_success",
}


class SimulationRequest(BaseModel):
    seed: int
    scenario_count: int


def _sanitize_payload(payload: dict | None) -> dict:
    """Return only whitelisted keys from an audit event payload."""
    if not payload:
        return {}
    return {k: v for k, v in payload.items() if k in SAFE_TIMELINE_KEYS}


# --- Metrics ---


@router.get("/metrics/recovery", response_model=DashboardMetrics)
async def get_recovery_metrics(
    mode: str | None = Query(
        None, description="Filter by mode: LIVE or SIMULATED. Defaults to ALL."
    ),
    session: AsyncSession = Depends(get_db),
):
    if mode and mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode. Must be one of: {sorted(VALID_MODES)}",
        )
    return await get_dashboard_metrics(session, mode)


# --- Simulation ---


@router.post("/simulation/run")
async def execute_simulation(
    request: SimulationRequest,
    session: AsyncSession = Depends(get_db),
):
    if not settings.ENABLE_SIMULATION_ENDPOINT:
        raise HTTPException(status_code=403, detail="Simulation endpoint is disabled.")

    if request.scenario_count > 100:
        raise HTTPException(
            status_code=400, detail="Maximum scenario_count is 100 to prevent abuse."
        )

    if request.scenario_count < 1:
        raise HTTPException(status_code=400, detail="Minimum scenario_count is 1.")

    result = await run_simulation(session, request.seed, request.scenario_count)
    return result


# --- Cases list ---


@router.get("/cases")
async def list_cases(
    mode: str | None = Query(None),
    case_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_db),
):
    if mode and mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode. Must be one of: {sorted(VALID_MODES)}",
        )
    if case_status and case_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {sorted(VALID_STATUSES)}",
        )

    stmt = select(RecoveryCase).order_by(desc(RecoveryCase.created_at))
    if mode:
        stmt = stmt.where(RecoveryCase.mode == mode)
    if case_status:
        stmt = stmt.where(RecoveryCase.status == case_status)

    stmt = stmt.limit(limit).offset(offset)
    cases = (await session.execute(stmt)).scalars().all()

    return [
        {
            "id": str(c.id),
            "original_payment_id": c.original_payment_id,
            "status": c.status,
            "mode": c.mode,
            "amount_at_risk": c.amount_at_risk,
            "amount_recovered": c.amount_recovered,
            "recovered_payment_id": c.recovered_payment_id,
            "stop_reason": c.stop_reason,
            "attempt_count": c.attempt_count,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        }
        for c in cases
    ]


# --- Case detail ---


@router.get("/cases/{case_id}")
async def get_case_detail(
    case_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Sanitized case detail — no raw customer PII, no raw LLM response, no payload_snapshot."""
    case = await session.get(RecoveryCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Original payment — safe fields only
    original_payment = await session.get(Payment, case.payment_fk)
    payment_safe = None
    if original_payment:
        payment_safe = {
            "razorpay_payment_id": original_payment.razorpay_payment_id,
            "amount": original_payment.amount,
            "currency": original_payment.currency,
            "method": original_payment.method,
            "error_code": original_payment.error_code,
            "error_reason": original_payment.error_reason,
            "error_source": original_payment.error_source,
            "error_step": original_payment.error_step,
            "status": original_payment.status,
            "failed_at": original_payment.failed_at.isoformat()
            if original_payment.failed_at
            else None,
        }

    # Customer capability — booleans only, no actual PII
    customer_capability = {"has_email": False, "has_phone": False}
    if case.customer_id:
        customer = await session.get(Customer, case.customer_id)
        if customer:
            customer_capability = {
                "has_email": bool(customer.email),
                "has_phone": bool(customer.phone),
            }

    # Decisions — no raw_llm_response
    decisions_stmt = (
        select(RecoveryDecision)
        .where(RecoveryDecision.case_id == case.id)
        .order_by(RecoveryDecision.attempt_number.asc())
    )
    decisions = (await session.execute(decisions_stmt)).scalars().all()
    decisions_safe = [
        {
            "id": str(d.id),
            "attempt_number": d.attempt_number,
            "recommended_action": d.recommended_action,
            "effective_action": d.effective_action,
            "policy_verdict": d.policy_verdict,
            "policy_reason": d.policy_reason,
            "reason": d.reason,
            "llm_confidence": d.llm_confidence,
            "heuristic_recovery_likelihood": d.heuristic_recovery_likelihood,
            "delay_hours": d.delay_hours,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in decisions
    ]

    # Actions — no metadata_
    actions_stmt = (
        select(RecoveryAction)
        .where(RecoveryAction.case_id == case.id)
        .order_by(RecoveryAction.attempt_number.asc())
    )
    actions = (await session.execute(actions_stmt)).scalars().all()
    actions_safe = [
        {
            "id": str(a.id),
            "attempt_number": a.attempt_number,
            "action_type": a.action_type,
            "status": a.status,
            "outcome": a.outcome,
            "razorpay_link_id": a.razorpay_link_id,
            "razorpay_link_reference_id": a.razorpay_link_reference_id,
            "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            "failure_reason": a.failure_reason,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in actions
    ]

    return {
        "case": {
            "id": str(case.id),
            "original_payment_id": case.original_payment_id,
            "status": case.status,
            "mode": case.mode,
            "amount_at_risk": case.amount_at_risk,
            "amount_recovered": case.amount_recovered,
            "recovered_payment_id": case.recovered_payment_id,
            "attempt_count": case.attempt_count,
            "stop_reason": case.stop_reason,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
            "recovery_window_expires_at": case.recovery_window_expires_at.isoformat()
            if case.recovery_window_expires_at
            else None,
        },
        "original_payment": payment_safe,
        "customer_capability": customer_capability,
        "decisions": decisions_safe,
        "actions": actions_safe,
    }


# --- Case timeline ---


@router.get("/cases/{case_id}/timeline")
async def get_case_timeline(
    case_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Sanitized timeline — only whitelisted payload keys are returned."""
    # Verify case exists
    case = await session.get(RecoveryCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    stmt = (
        select(AuditEvent)
        .where(AuditEvent.case_id == case_id)
        .order_by(AuditEvent.created_at.asc())
    )
    events = (await session.execute(stmt)).scalars().all()

    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "actor": e.actor,
            "mode": e.mode,
            "payload": _sanitize_payload(e.payload),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
