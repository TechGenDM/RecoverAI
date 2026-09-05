"""M3 Recovery Executor — orchestrates Payment Link creation and reconciliation.

This module is the M3 entrypoint. It discovers pending SEND_PAYMENT_LINK
decisions, claims them atomically, executes via the appropriate executor
(LIVE or SIMULATED), and persists results with terminal-state protection.

INVARIANTS (Rev 4):
- M3 NEVER increments attempt_count (M2-owned)
- M3 NEVER calls the LLM
- Unknown outcomes remain EXECUTING, never WAITING
- Category B reconciliation runs before Category A execution
- GET by reference_id is the primary reconciliation mechanism
- No DB lock during Razorpay API calls
- Only performs EXECUTING → LINK_SENT; never overwrites RECOVERED
- Exact amount matching for recovery
- No customer notifications
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.customer import Customer
from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.models.recovery_decision import RecoveryDecision
from app.services.executor import ExecutionResult, get_executor

logger = logging.getLogger(__name__)


def _generate_reference_id(case_id: uuid.UUID, attempt_number: int) -> str:
    """Generate a deterministic, unique reference_id for a Payment Link.

    Format: rc-{case_id_hex[:24]}-a{attempt_number}
    Always <= 40 characters (Razorpay limit).
    """
    ref = f"rc-{case_id.hex[:24]}-a{attempt_number}"
    assert len(ref) <= 40, f"reference_id too long: {len(ref)} chars"
    return ref


def _generate_idempotency_key(case_id: uuid.UUID, attempt_number: int) -> str:
    """Generate a deterministic idempotency key for a RecoveryAction."""
    return f"{case_id}-{attempt_number}-exec"


def _build_customer_block(customer: Customer | None) -> dict[str, str] | None:
    """Build the optional Razorpay customer block from our Customer model.

    Returns None if no usable contact info exists (link creation still works).
    """
    if customer is None:
        return None
    block: dict[str, str] = {}
    if customer.email:
        block["email"] = customer.email
    if customer.phone:
        block["contact"] = customer.phone
    if customer.name:
        block["name"] = customer.name
    return block if block else None


async def discover_new_executions(
    session: AsyncSession,
    case_id: uuid.UUID | str | None = None,
    mode: str | None = None,
) -> list[tuple[RecoveryDecision, RecoveryCase]]:
    """Category A: Find SEND_PAYMENT_LINK decisions with no RecoveryAction.

    Returns (decision, case) pairs with FOR UPDATE SKIP LOCKED on cases.
    Optionally scoped to a single case_id and/or execution mode (e.g. SIMULATED).
    """
    stmt = (
        select(RecoveryDecision, RecoveryCase)
        .join(RecoveryCase, RecoveryDecision.case_id == RecoveryCase.id)
        .outerjoin(RecoveryAction, RecoveryAction.decision_id == RecoveryDecision.id)
        .where(
            and_(
                RecoveryDecision.effective_action == "SEND_PAYMENT_LINK",
                RecoveryCase.status == "ANALYSING",
                RecoveryAction.id.is_(None),
            )
        )
    )
    if case_id is not None:
        cid = uuid.UUID(str(case_id)) if not isinstance(case_id, uuid.UUID) else case_id
        stmt = stmt.where(RecoveryCase.id == cid)
    if mode is not None:
        stmt = stmt.where(RecoveryCase.mode == mode)

    stmt = stmt.with_for_update(of=RecoveryCase, skip_locked=True).limit(
        settings.SCHEDULER_BATCH_SIZE
    )
    result = await session.execute(stmt)
    return list(result.tuples().all())


async def discover_reconciliation_targets(
    session: AsyncSession,
    case_id: uuid.UUID | str | None = None,
    mode: str | None = None,
) -> list[tuple[RecoveryAction, RecoveryCase]]:
    """Category B: Find EXECUTING actions with unknown outcomes.

    Returns (action, case) pairs with FOR UPDATE SKIP LOCKED on cases.
    Optionally scoped to a single case_id and/or execution mode (e.g. SIMULATED).
    """
    stmt = (
        select(RecoveryAction, RecoveryCase)
        .join(RecoveryCase, RecoveryAction.case_id == RecoveryCase.id)
        .where(
            and_(
                RecoveryAction.action_type == "SEND_PAYMENT_LINK",
                RecoveryAction.status == "EXECUTING",
                RecoveryCase.status == "EXECUTING",
            )
        )
    )
    if case_id is not None:
        cid = uuid.UUID(str(case_id)) if not isinstance(case_id, uuid.UUID) else case_id
        stmt = stmt.where(RecoveryCase.id == cid)
    if mode is not None:
        stmt = stmt.where(RecoveryCase.mode == mode)

    stmt = stmt.with_for_update(of=RecoveryCase, skip_locked=True).limit(
        settings.SCHEDULER_BATCH_SIZE
    )
    result = await session.execute(stmt)
    return list(result.tuples().all())


async def claim_for_execution(
    session: AsyncSession,
    decision: RecoveryDecision,
    case: RecoveryCase,
    now: datetime | None = None,
) -> RecoveryAction | None:
    """Atomically create a RecoveryAction and transition case to EXECUTING.

    Returns the created RecoveryAction, or None if the case should be skipped
    (e.g. insufficient time remaining).
    """
    if now is None:
        now = datetime.now(UTC)
    min_validity = timedelta(minutes=settings.PAYMENT_LINK_MIN_VALIDITY_MINUTES)

    reference_id = _generate_reference_id(case.id, case.attempt_count)
    idempotency_key = _generate_idempotency_key(case.id, case.attempt_count)

    # Check if recovery window has enough time remaining
    if case.recovery_window_expires_at <= now + min_validity:
        logger.warning(
            "Case %s: insufficient time remaining (expires %s)",
            case.id,
            case.recovery_window_expires_at,
        )
        # Create FAILED action for audit trail
        failed_action = RecoveryAction(
            case_id=case.id,
            decision_id=decision.id,
            attempt_number=case.attempt_count,
            action_type="SEND_PAYMENT_LINK",
            status="FAILED",
            idempotency_key=idempotency_key,
            razorpay_link_reference_id=reference_id,
            failure_reason="INSUFFICIENT_TIME",
        )
        session.add(failed_action)
        case.status = "STOPPED"
        case.stop_reason = "Insufficient recovery window remaining"
        case.resolved_at = now
        session.add(
            AuditEvent(
                case_id=case.id,
                action_id=failed_action.id,
                event_type="RECOVERY_ACTION_FAILED",
                actor="executor",
                mode=case.mode,
                payload={
                    "reason": "INSUFFICIENT_TIME",
                    "window_expires_at": case.recovery_window_expires_at.isoformat(),
                    "min_validity_minutes": settings.PAYMENT_LINK_MIN_VALIDITY_MINUTES,
                },
            )
        )
        return None

    # Create EXECUTING action
    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idempotency_key,
        razorpay_link_reference_id=reference_id,
    )
    session.add(action)
    case.status = "EXECUTING"
    session.add(
        AuditEvent(
            case_id=case.id,
            action_id=action.id,
            event_type="RECOVERY_ACTION_CREATED",
            actor="executor",
            mode=case.mode,
            payload={
                "reference_id": reference_id,
                "idempotency_key": idempotency_key,
                "attempt_number": case.attempt_count,
            },
        )
    )
    return action


async def _persist_execution_result(
    session: AsyncSession,
    action_id: uuid.UUID,
    case_id: uuid.UUID,
    result: ExecutionResult,
    mode: str,
    now: datetime | None = None,
) -> None:
    """Persist the execution result with terminal-state protection.

    Re-reads the case with FOR UPDATE to prevent overwriting RECOVERED.
    M3 may only perform EXECUTING → LINK_SENT.
    """
    if now is None:
        now = datetime.now(UTC)

    # Re-read with lock to protect against webhook race
    case = await session.get(RecoveryCase, case_id, with_for_update=True)
    action = await session.get(RecoveryAction, action_id)

    if case is None or action is None:
        logger.error(
            "Case %s or action %s not found during result persistence",
            case_id,
            action_id,
        )
        return

    if case.status == "RECOVERED":
        # Webhook already marked recovered while we waited for Razorpay
        logger.warning(
            "Case %s already RECOVERED — persisting link metadata only",
            case_id,
        )
        if action.razorpay_link_id is None and result.razorpay_link_id:
            action.razorpay_link_id = result.razorpay_link_id
            action.razorpay_link_short_url = result.short_url
        session.add(
            AuditEvent(
                case_id=case_id,
                action_id=action_id,
                event_type="RECOVERY_ACTION_SUCCEEDED_AFTER_RECOVERY",
                actor="executor",
                mode=mode,
                payload={"razorpay_link_id": result.razorpay_link_id},
            )
        )
        return

    if case.status not in ("EXECUTING", "STOPPED"):
        # Unexpected state — do not modify

        logger.warning(
            "Case %s in unexpected state %s during result persistence",
            case_id,
            case.status,
        )
        session.add(
            AuditEvent(
                case_id=case_id,
                action_id=action_id,
                event_type="UNEXPECTED_STATE_DURING_EXECUTION",
                actor="executor",
                mode=mode,
                payload={
                    "current_status": case.status,
                    "result_success": result.success,
                },
            )
        )
        return

    if result.success:
        action.status = "SUCCESS"
        action.razorpay_link_id = result.razorpay_link_id
        action.razorpay_link_short_url = result.short_url
        if result.expire_by:
            action.razorpay_link_expire_by = datetime.fromtimestamp(
                result.expire_by, tz=UTC
            )
        action.executed_at = now
        # Only transition EXECUTING → LINK_SENT
        if case.status == "EXECUTING":
            case.status = "LINK_SENT"
        session.add(
            AuditEvent(
                case_id=case_id,
                action_id=action_id,
                event_type="RECOVERY_ACTION_SUCCEEDED",
                actor="executor",
                mode=mode,
                payload={
                    "razorpay_link_id": result.razorpay_link_id,
                    "short_url": result.short_url,
                    "reference_id": result.reference_id,
                },
            )
        )

    elif result.definite_failure:
        action.status = "FAILED"
        action.failure_reason = result.error
        if case.status == "EXECUTING":
            case.status = "STOPPED"
            case.stop_reason = result.error
            case.resolved_at = now
        session.add(
            AuditEvent(
                case_id=case_id,
                action_id=action_id,
                event_type="RECOVERY_ACTION_FAILED",
                actor="executor",
                mode=mode,
                payload={"error": result.error},
            )
        )

    else:
        # Unknown outcome (timeout) — NO state change
        # Case remains EXECUTING, action remains EXECUTING
        session.add(
            AuditEvent(
                case_id=case_id,
                action_id=action_id,
                event_type="RECOVERY_ACTION_UNKNOWN_OUTCOME",
                actor="executor",
                mode=mode,
                payload={"error": result.error},
            )
        )


async def _reconcile_action(
    session: AsyncSession,
    action: RecoveryAction,
    case: RecoveryCase,
    now: datetime | None = None,
) -> None:
    """Reconcile an EXECUTING action with unknown outcome.

    Primary mechanism: GET /v1/payment_links/?reference_id=<ref>
    If GET times out, DO NOT POST — leave EXECUTING for next tick.
    """
    executor = get_executor(case.mode)
    reference_id = action.razorpay_link_reference_id

    if not reference_id:
        logger.error("Action %s has no reference_id — cannot reconcile", action.id)
        return

    if action.razorpay_link_id:
        # We have the plink_id — fetch directly
        result = await executor.reconcile_by_id(action.razorpay_link_id)
        if result.timeout:
            logger.warning(
                "Reconcile by ID timeout for action %s — will retry",
                action.id,
            )
            async with session.begin():
                session.add(
                    AuditEvent(
                        case_id=case.id,
                        action_id=action.id,
                        event_type="RECONCILIATION_TIMEOUT",
                        actor="executor",
                        mode=case.mode,
                        payload={"plink_id": action.razorpay_link_id},
                    )
                )
            return

        if result.definite_failure:
            # plink_id stored but link doesn't exist (404)
            async with session.begin():
                # Re-read objects in the new transaction
                action = await session.get(RecoveryAction, action.id)
                case = await session.get(RecoveryCase, case.id)
                action.status = "FAILED"
                action.failure_reason = f"Reconciliation failed: {result.error}"
                case.status = "STOPPED"
                case.stop_reason = f"Payment link not found: {result.error}"
                case.resolved_at = now if now else datetime.now(UTC)
                session.add(
                    AuditEvent(
                        case_id=case.id,
                        action_id=action.id,
                        event_type="PAYMENT_LINK_RECONCILIATION_ANOMALY",
                        actor="executor",
                        mode=case.mode,
                        payload={"error": result.error},
                    )
                )
            return

        async with session.begin():
            await _persist_execution_result(
                session, action.id, case.id, result, case.mode, now=now
            )
        return

    # No plink_id — use GET by reference_id (primary reconciliation)
    results = await executor.reconcile_by_reference_id(reference_id)

    # Check for timeout
    if len(results) == 1 and results[0].timeout:
        logger.warning(
            "Reconcile GET by reference_id timeout for action %s — NO POST, will retry",
            action.id,
        )
        async with session.begin():
            session.add(
                AuditEvent(
                    case_id=case.id,
                    action_id=action.id,
                    event_type="RECONCILIATION_GET_TIMEOUT",
                    actor="executor",
                    mode=case.mode,
                    payload={"reference_id": reference_id},
                )
            )
        return

    # Exact match verification
    exact_matches = [r for r in results if r.success and r.reference_id == reference_id]

    if len(exact_matches) == 1:
        # Link exists — reconcile without creating another
        logger.info(
            "Reconciled action %s — link %s already exists",
            action.id,
            exact_matches[0].razorpay_link_id,
        )
        async with session.begin():
            await _persist_execution_result(
                session, action.id, case.id, exact_matches[0], case.mode, now=now
            )
            session.add(
                AuditEvent(
                    case_id=case.id,
                    action_id=action.id,
                    event_type="PAYMENT_LINK_RECONCILED",
                    actor="executor",
                    mode=case.mode,
                    payload={
                        "razorpay_link_id": exact_matches[0].razorpay_link_id,
                        "reference_id": reference_id,
                    },
                )
            )
        return

    if len(exact_matches) > 1:
        # Multiple matches — anomaly
        logger.error(
            "Reconciliation anomaly: %d links match reference_id %s",
            len(exact_matches),
            reference_id,
        )
        async with session.begin():
            action = await session.get(RecoveryAction, action.id)
            case = await session.get(RecoveryCase, case.id)
            action.status = "FAILED"
            action.failure_reason = (
                f"RECONCILIATION_ANOMALY: {len(exact_matches)} matching links"
            )
            case.status = "STOPPED"
            case.stop_reason = (
                f"Multiple payment links found for reference_id {reference_id}"
            )
            case.resolved_at = now if now else datetime.now(UTC)
            session.add(
                AuditEvent(
                    case_id=case.id,
                    action_id=action.id,
                    event_type="RECONCILIATION_ANOMALY_MULTIPLE_LINKS",
                    actor="executor",
                    mode=case.mode,
                    payload={
                        "reference_id": reference_id,
                        "match_count": len(exact_matches),
                    },
                )
            )
        return

    # Zero matches — safe to create with the same reference_id
    logger.info(
        "No existing link for reference_id %s — creating new link",
        reference_id,
    )
    async with session.begin():
        customer_block = await _load_customer_block(session, case)
    expire_by = _compute_expire_by(case, now=now)

    if expire_by is None:
        async with session.begin():
            action = await session.get(RecoveryAction, action.id)
            case = await session.get(RecoveryCase, case.id)
            action.status = "FAILED"
            action.failure_reason = "INSUFFICIENT_TIME_ON_RECONCILIATION"
            case.status = "STOPPED"
            case.stop_reason = "Insufficient recovery window on reconciliation"
            case.resolved_at = now if now else datetime.now(UTC)
            session.add(
                AuditEvent(
                    case_id=case.id,
                    action_id=action.id,
                    event_type="RECOVERY_ACTION_FAILED",
                    actor="executor",
                    mode=case.mode,
                    payload={"reason": "INSUFFICIENT_TIME_ON_RECONCILIATION"},
                )
            )
        return

    result = await executor.execute(
        amount=case.amount_at_risk,
        currency="INR",
        reference_id=reference_id,
        expire_by=expire_by,
        description="Recovery payment",
        customer=customer_block,
    )

    # Handle duplicate reference_id error (defensive fallback)
    if (
        result.definite_failure
        and result.error
        and "DUPLICATE_REFERENCE" in result.error
    ):
        logger.info(
            "Duplicate reference_id on retry for %s — attempting GET reconciliation",
            reference_id,
        )
        followup = await executor.reconcile_by_reference_id(reference_id)
        followup_matches = [
            r for r in followup if r.success and r.reference_id == reference_id
        ]
        if len(followup_matches) == 1:
            result = followup_matches[0]
        else:
            # Cannot reconcile — leave for next tick
            async with session.begin():
                session.add(
                    AuditEvent(
                        case_id=case.id,
                        action_id=action.id,
                        event_type="DUPLICATE_REFERENCE_RECONCILIATION_FAILED",
                        actor="executor",
                        mode=case.mode,
                        payload={"reference_id": reference_id},
                    )
                )
            return

    async with session.begin():
        await _persist_execution_result(
            session, action.id, case.id, result, case.mode, now=now
        )


async def _execute_new_decision(
    session: AsyncSession,
    decision: RecoveryDecision,
    case: RecoveryCase,
    now: datetime | None = None,
) -> None:
    """Execute a newly claimed SEND_PAYMENT_LINK decision."""
    # Phase 1: Atomic claim (inside caller's transaction)
    async with session.begin_nested():
        action = await claim_for_execution(session, decision, case, now=now)
        if action is None:
            return  # Skipped (insufficient time)
        await session.flush()  # Ensure action.id is populated
        action_id = action.id
        case_id = case.id
        case_mode = case.mode

    # Flush to ensure the action is visible
    await session.commit()

    # Phase 2: External API call (NO DB lock held)
    executor = get_executor(case_mode)

    # Re-read case for execution data
    async with session.begin():
        case = await session.get(RecoveryCase, case_id)
        action = await session.get(RecoveryAction, action_id)
        if case is None or action is None:
            return

        reference_id = action.razorpay_link_reference_id
        if not reference_id:
            return

        customer_block = await _load_customer_block(session, case)
        expire_by = _compute_expire_by(case, now=now)

    if expire_by is None:
        async with session.begin():
            action = await session.get(RecoveryAction, action_id)
            case = await session.get(RecoveryCase, case_id)
            if action and case:
                action.status = "FAILED"
                action.failure_reason = "INSUFFICIENT_TIME_AT_EXECUTION"
                if case.status == "EXECUTING":
                    case.status = "STOPPED"
                    case.stop_reason = "Recovery window expired before execution"
                    case.resolved_at = now if now else datetime.now(UTC)
                session.add(
                    AuditEvent(
                        case_id=case_id,
                        action_id=action_id,
                        event_type="RECOVERY_ACTION_FAILED",
                        actor="executor",
                        mode=case_mode,
                        payload={"reason": "INSUFFICIENT_TIME_AT_EXECUTION"},
                    )
                )
        return

    result = await executor.execute(
        amount=case.amount_at_risk,
        currency="INR",
        reference_id=reference_id,
        expire_by=expire_by,
        description="Recovery payment",
        customer=customer_block,
    )

    # Handle duplicate reference_id error (defensive fallback)
    if (
        result.definite_failure
        and result.error
        and "DUPLICATE_REFERENCE" in result.error
    ):
        followup = await executor.reconcile_by_reference_id(reference_id)
        followup_matches = [
            r for r in followup if r.success and r.reference_id == reference_id
        ]
        if len(followup_matches) == 1:
            result = followup_matches[0]

    # Phase 3: Persist result (with terminal-state protection)
    async with session.begin():
        await _persist_execution_result(
            session, action_id, case_id, result, case_mode, now=now
        )


def _compute_expire_by(case: RecoveryCase, now: datetime | None = None) -> int | None:
    """Compute the expire_by Unix timestamp for a Payment Link.

    Returns None if insufficient time remains.
    """
    if now is None:
        now = datetime.now(UTC)
    min_validity = timedelta(minutes=settings.PAYMENT_LINK_MIN_VALIDITY_MINUTES)

    if case.recovery_window_expires_at <= now + min_validity:
        return None

    link_expiry = now + timedelta(hours=settings.RECOVERY_LINK_EXPIRY_HOURS)
    effective_expiry = min(link_expiry, case.recovery_window_expires_at)
    return int(effective_expiry.timestamp())


async def _load_customer_block(
    session: AsyncSession, case: RecoveryCase
) -> dict[str, str] | None:
    """Load customer data for the Payment Link customer block."""
    if case.customer_id is None:
        return None
    customer = await session.get(Customer, case.customer_id)
    return _build_customer_block(customer)


async def run_execution_phase(
    session: AsyncSession,
    now: datetime | None = None,
    case_id: uuid.UUID | str | None = None,
    mode: str | None = None,
) -> int:
    """M3 scheduler entrypoint.

    Processes Category B (reconciliation) first, then Category A (new execution).
    Optionally scoped to a specific case_id and/or execution mode (e.g. SIMULATED).
    Returns total number of actions processed.
    """
    processed = 0

    # Category B: Reconcile EXECUTING actions first
    try:
        async with session.begin():
            reconciliation_targets = await discover_reconciliation_targets(
                session, case_id=case_id, mode=mode
            )
    except Exception:
        logger.exception("Failed to discover reconciliation targets")
        reconciliation_targets = []

    for action, case in reconciliation_targets:
        if mode is not None and case.mode != mode:
            logger.warning(
                "Skipping action %s for case %s: mode mismatch (%s != %s)",
                action.id,
                case.id,
                case.mode,
                mode,
            )
            continue
        try:
            await _reconcile_action(session, action, case, now=now)
            processed += 1
        except Exception:
            logger.exception(
                "Failed to reconcile action %s for case %s",
                action.id,
                case.id,
            )

    # Category A: Execute new decisions
    try:
        async with session.begin():
            new_targets = await discover_new_executions(
                session, case_id=case_id, mode=mode
            )
    except Exception:
        logger.exception("Failed to discover new executions")
        new_targets = []

    for decision, case in new_targets:
        if mode is not None and case.mode != mode:
            logger.warning(
                "Skipping decision %s for case %s: mode mismatch (%s != %s)",
                decision.id,
                case.id,
                case.mode,
                mode,
            )
            continue
        try:
            await _execute_new_decision(session, decision, case, now=now)
            processed += 1
        except Exception:
            logger.exception(
                "Failed to execute decision %s for case %s",
                decision.id,
                case.id,
            )

    if processed > 0:
        logger.info("M3 execution phase processed %d actions", processed)
    return processed
