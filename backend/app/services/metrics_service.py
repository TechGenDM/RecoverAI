from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, RecoveryAction, RecoveryCase, RecoveryDecision
from app.schemas.dashboard import DashboardMetrics, FailureReasonMetrics

VALID_STATUSES = [
    "CREATED",
    "ANALYSING",
    "WAITING",
    "EXECUTING",
    "LINK_SENT",
    "RECOVERED",
    "STOPPED",
    "ESCALATED",
]


async def get_dashboard_metrics(
    session: AsyncSession, mode: str | None = None
) -> DashboardMetrics:
    # 1. Case-Level Funnel Metrics
    case_filters = []
    if mode and mode != "ALL":
        case_filters.append(RecoveryCase.mode == mode)

    case_stmt = select(
        func.count(RecoveryCase.id).label("total_cases"),
        func.sum(RecoveryCase.amount_at_risk).label("amount_at_risk_paise"),
        func.sum(RecoveryCase.amount_recovered).label("amount_recovered_paise"),
        func.sum(case((RecoveryCase.status == "RECOVERED", 1), else_=0)).label(
            "recovered_cases"
        ),
        func.sum(case((RecoveryCase.status == "STOPPED", 1), else_=0)).label(
            "stopped_cases"
        ),
        func.sum(case((RecoveryCase.status == "ESCALATED", 1), else_=0)).label(
            "escalated_cases"
        ),
    ).where(*case_filters)

    case_res = (await session.execute(case_stmt)).first()

    total_cases = case_res.total_cases or 0
    amount_at_risk = case_res.amount_at_risk_paise or 0
    amount_recovered = case_res.amount_recovered_paise or 0
    recovered_cases = case_res.recovered_cases or 0
    stopped_cases = case_res.stopped_cases or 0
    escalated_cases = case_res.escalated_cases or 0

    recovery_rate_by_count = (
        (recovered_cases / total_cases) if total_cases > 0 else None
    )
    recovery_rate_by_amount = (
        (amount_recovered / amount_at_risk) if amount_at_risk > 0 else None
    )

    # 1b. Case Status Distribution (all 8 statuses guaranteed present)
    status_map = {s: 0 for s in VALID_STATUSES}
    status_stmt = (
        select(RecoveryCase.status, func.count(RecoveryCase.id))
        .where(*case_filters)
        .group_by(RecoveryCase.status)
    )
    status_rows = (await session.execute(status_stmt)).all()
    for st, cnt in status_rows:
        if st in status_map:
            status_map[st] = cnt

    # 1c. Breakdown by Original Payment Failure Reason
    subq = (
        select(
            RecoveryCase.id,
            RecoveryCase.status,
            RecoveryCase.amount_at_risk,
            RecoveryCase.amount_recovered,
            func.coalesce(Payment.error_reason, "UNKNOWN").label("reason"),
        )
        .select_from(RecoveryCase)
        .join(Payment, RecoveryCase.payment_fk == Payment.id)
        .where(*case_filters)
        .subquery()
    )
    failure_stmt = select(
        subq.c.reason,
        func.count(subq.c.id).label("total"),
        func.sum(case((subq.c.status == "RECOVERED", 1), else_=0)).label("recovered"),
        func.sum(subq.c.amount_at_risk).label("at_risk"),
        func.sum(subq.c.amount_recovered).label("recovered_amount"),
    ).group_by(subq.c.reason)
    failure_rows = (await session.execute(failure_stmt)).all()

    failure_reason_map: dict[str, FailureReasonMetrics] = {}
    for row in failure_rows:
        tot = row.total or 0
        rec = row.recovered or 0
        failure_reason_map[row.reason] = FailureReasonMetrics(
            total_cases=tot,
            recovered_cases=rec,
            recovery_rate_by_count=(rec / tot) if tot > 0 else None,
            amount_at_risk_paise=row.at_risk or 0,
            amount_recovered_paise=row.recovered_amount or 0,
        )

    # 2. Case-Level Final Interventions (Latest Decision per case)
    # Pure SQLAlchemy window function & subquery — no raw SQL string, fully parameterized
    rn_col = (
        func.row_number()
        .over(
            partition_by=RecoveryDecision.case_id,
            order_by=[
                RecoveryDecision.attempt_number.desc(),
                RecoveryDecision.created_at.desc(),
                RecoveryDecision.id.desc(),
            ],
        )
        .label("rn")
    )

    latest_decisions_subq = (
        select(
            RecoveryDecision.effective_action,
            rn_col,
        )
        .join(RecoveryCase, RecoveryDecision.case_id == RecoveryCase.id)
        .where(*case_filters)
        .subquery()
    )

    intervention_stmt = select(
        func.sum(
            case(
                (
                    latest_decisions_subq.c.effective_action == "SEND_PAYMENT_LINK",
                    1,
                ),
                else_=0,
            )
        ).label("link_count"),
        func.sum(
            case((latest_decisions_subq.c.effective_action == "WAIT", 1), else_=0)
        ).label("wait_count"),
        func.sum(
            case(
                (latest_decisions_subq.c.effective_action == "ESCALATE", 1),
                else_=0,
            )
        ).label("escalate_count"),
        func.sum(
            case((latest_decisions_subq.c.effective_action == "STOP", 1), else_=0)
        ).label("stop_count"),
    ).where(latest_decisions_subq.c.rn == 1)

    interventions_res = (await session.execute(intervention_stmt)).first()

    # 3. Action / Decision Attempt-Level Metrics
    action_filters = []
    decision_filters = []
    if mode and mode != "ALL":
        action_filters.append(RecoveryCase.mode == mode)
        decision_filters.append(RecoveryCase.mode == mode)

    # Action metrics
    action_stmt = (
        select(
            func.sum(
                case(
                    (
                        (RecoveryAction.action_type == "SEND_PAYMENT_LINK")
                        & (RecoveryAction.status == "SUCCESS")
                        & (RecoveryAction.razorpay_link_id.is_not(None)),
                        1,
                    ),
                    else_=0,
                )
            ).label("links_created"),
            func.sum(
                case(
                    (
                        (RecoveryAction.action_type == "SEND_PAYMENT_LINK")
                        & (RecoveryAction.outcome == "RECOVERED"),
                        1,
                    ),
                    else_=0,
                )
            ).label("links_paid"),
            func.sum(
                case(
                    (
                        (RecoveryAction.action_type == "SEND_PAYMENT_LINK")
                        & (RecoveryAction.outcome == "EXPIRED"),
                        1,
                    ),
                    else_=0,
                )
            ).label("links_expired"),
            func.sum(
                case(
                    (
                        (RecoveryAction.action_type == "SEND_PAYMENT_LINK")
                        & (RecoveryAction.outcome == "CANCELLED"),
                        1,
                    ),
                    else_=0,
                )
            ).label("links_cancelled"),
        )
        .select_from(RecoveryAction)
        .join(RecoveryCase, RecoveryAction.case_id == RecoveryCase.id)
        .where(*action_filters)
    )

    action_res = (await session.execute(action_stmt)).first()

    # Decision metrics
    decision_stmt = (
        select(
            func.count(RecoveryDecision.id).label("total"),
            func.sum(
                case(
                    (RecoveryDecision.effective_action == "SEND_PAYMENT_LINK", 1),
                    else_=0,
                )
            ).label("send_link"),
            func.sum(
                case((RecoveryDecision.effective_action == "WAIT", 1), else_=0)
            ).label("wait"),
            func.sum(
                case((RecoveryDecision.effective_action == "ESCALATE", 1), else_=0)
            ).label("escalate"),
            func.sum(
                case((RecoveryDecision.effective_action == "STOP", 1), else_=0)
            ).label("stop"),
        )
        .select_from(RecoveryDecision)
        .join(RecoveryCase, RecoveryDecision.case_id == RecoveryCase.id)
        .where(*decision_filters)
    )

    decision_res = (await session.execute(decision_stmt)).first()

    return DashboardMetrics(
        mode=mode or "ALL",
        total_cases=total_cases,
        amount_at_risk_paise=amount_at_risk,
        amount_recovered_paise=amount_recovered,
        recovered_cases=recovered_cases,
        stopped_cases=stopped_cases,
        escalated_cases=escalated_cases,
        recovery_rate_by_count=recovery_rate_by_count,
        recovery_rate_by_amount=recovery_rate_by_amount,
        cases_by_status=status_map,
        cases_intervened_link=interventions_res.link_count or 0
        if interventions_res
        else 0,
        cases_intervened_wait=interventions_res.wait_count or 0
        if interventions_res
        else 0,
        cases_intervened_escalate=interventions_res.escalate_count or 0
        if interventions_res
        else 0,
        cases_intervened_stop=interventions_res.stop_count or 0
        if interventions_res
        else 0,
        payment_links_created=action_res.links_created or 0 if action_res else 0,
        payment_links_paid=action_res.links_paid or 0 if action_res else 0,
        payment_links_expired=action_res.links_expired or 0 if action_res else 0,
        payment_links_cancelled=action_res.links_cancelled or 0 if action_res else 0,
        decisions_total=decision_res.total or 0 if decision_res else 0,
        decisions_send_link=decision_res.send_link or 0 if decision_res else 0,
        decisions_wait=decision_res.wait or 0 if decision_res else 0,
        decisions_escalate=decision_res.escalate or 0 if decision_res else 0,
        decisions_stop=decision_res.stop or 0 if decision_res else 0,
        recovery_by_failure_reason=failure_reason_map,
    )
