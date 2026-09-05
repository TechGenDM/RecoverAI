from datetime import UTC, datetime

from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Payment, RecoveryAction, RecoveryCase
from app.schemas import (
    CustomerContext,
    HistoricalContext,
    PaymentContext,
    PreviousAction,
    RecoveryContext,
    RecoveryHistory,
    TimeContext,
)


async def build_recovery_context(
    session: AsyncSession, case: RecoveryCase, now: datetime | None = None
) -> RecoveryContext:
    if now is None:
        now = datetime.now(UTC)

    # 1. Eagerly load relationships if not already loaded (though scheduler should ideally load them)
    if "payment" not in case.__dict__:
        await session.refresh(case, ["payment"])
    payment = case.payment

    if "customer" not in payment.__dict__:
        await session.refresh(payment, ["customer"])
    customer = payment.customer

    # 2. Base payment context
    payment_ctx = PaymentContext(
        payment_id=payment.razorpay_payment_id,
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        bank=payment.bank,
        card_network=payment.card_network,
        international=payment.international,
        order_id=payment.order_id,
        error_code=payment.error_code,
        error_reason=payment.error_reason,
        error_source=payment.error_source,
        error_step=payment.error_step,
        failed_at=payment.created_at.isoformat(),
    )

    # 3. Customer context
    customer_ctx = CustomerContext(
        has_email=bool(customer.email) if customer else False,
        has_phone=bool(customer.phone) if customer else False,
        has_profile=False,  # M1 scope doesn't have profiles yet, extend later if needed
    )

    # 4. Historical Context (Only captured/failed)
    total, succ, fail = 0, 0, 0
    avg_amt, days_since = None, None
    repeated_fails = 0
    success_rate = None
    
    if customer:
        hist_stmt = select(
            func.count(Payment.id).label("total"),
            func.sum(sql_case((Payment.status == "captured", 1), else_=0)).label(
                "success_count"
            ),
            func.sum(sql_case((Payment.status == "failed", 1), else_=0)).label(
                "failed_count"
            ),
            func.avg(Payment.amount).label("avg_amount"),
            func.max(Payment.created_at).label("last_payment_date"),
        ).where(
            Payment.customer_id == customer.id,
            Payment.id != payment.id,
            Payment.status.in_(["captured", "failed"]),
        )
        hist_res = (await session.execute(hist_stmt)).first()

        total = hist_res.total if hist_res and hist_res.total else 0
        succ = hist_res.success_count if hist_res and hist_res.success_count else 0
        fail = hist_res.failed_count if hist_res and hist_res.failed_count else 0
        avg_amt = float(hist_res.avg_amount) if hist_res and hist_res.avg_amount else None

        success_rate = (succ / total) if total > 0 else None

        if hist_res and hist_res.last_payment_date:
            days_since = (now - hist_res.last_payment_date).total_seconds() / 86400.0

        if payment.method:
            repeated_stmt = select(func.count(Payment.id)).where(
                Payment.customer_id == customer.id,
                Payment.id != payment.id,
                Payment.status == "failed",
                Payment.method == payment.method,
            )
            repeated_fails = (await session.execute(repeated_stmt)).scalar() or 0

    historical_ctx = HistoricalContext(
        total_payments=total,
        successful_payments=succ,
        failed_payments=fail,
        success_rate=success_rate,
        has_previously_paid=succ > 0,
        has_previously_failed=fail > 0,
        avg_payment_amount=avg_amt,
        days_since_last_payment=days_since,
        repeated_method_failures=repeated_fails,
    )

    # 5. Recovery History
    actions_stmt = (
        select(RecoveryAction)
        .where(RecoveryAction.case_id == case.id)
        .order_by(RecoveryAction.created_at.asc())
    )
    actions = (await session.execute(actions_stmt)).scalars().all()

    prev_actions = []
    for i, a in enumerate(actions):
        prev_actions.append(
            PreviousAction(
                attempt_number=i + 1,
                action_type=a.action_type,
                executed_at=a.executed_at.isoformat() if a.executed_at else None,
                outcome=a.status,
            )
        )

    recovery_history = RecoveryHistory(
        attempt_count=len(prev_actions), previous_actions=prev_actions
    )

    # 6. Time Context
    # Expiry is 72 hours from payment creation
    window_hours = settings.RECOVERY_MAX_WINDOW_HOURS
    expiry_time = payment.created_at.timestamp() + (window_hours * 3600)

    hours_since = (now - payment.created_at).total_seconds() / 3600.0
    hours_rem = window_hours - hours_since

    time_ctx = TimeContext(
        failed_at=payment.created_at.isoformat(),
        evaluation_time=now.isoformat(),
        recovery_window_expires_at=datetime.fromtimestamp(expiry_time, UTC).isoformat(),
        hours_remaining=max(0.0, hours_rem),
        hours_since_failure=hours_since,
        window_expired=(hours_rem <= 0),
    )

    # 7. Capabilities
    can_generate_payment_link = False
    if customer:
        can_generate_payment_link = bool(customer.email or hasattr(customer, 'contact') and customer.contact or customer.phone)

    return RecoveryContext(
        case_id=str(case.id),
        attempt_number=len(prev_actions) + 1,
        payment=payment_ctx,
        customer=customer_ctx,
        historical=historical_ctx,
        recovery_history=recovery_history,
        time=time_ctx,
        can_generate_payment_link=can_generate_payment_link,
        can_auto_notify_customer=can_generate_payment_link,
        max_attempts=settings.RECOVERY_MAX_ATTEMPTS,
    )
