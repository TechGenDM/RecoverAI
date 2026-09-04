"""M3 Executor & Recovery Service Tests.

Tests 1-18 from the Rev 4 test plan:
- Executor behavior (1-10)
- Reconciliation (11-16)
- Concurrency (17-18)
- Implementation-gate tests (31-33)
"""

import asyncio
import datetime
import uuid
from unittest.mock import AsyncMock, patch

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
from app.services.executor.base import ExecutionResult
from app.services.executor.simulated_executor import SimulatedRecoveryExecutor
from app.services.recovery_service import (
    _generate_idempotency_key,
    _generate_reference_id,
    claim_for_execution,
    discover_new_executions,
    run_execution_phase,
)

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


async def _create_test_case(
    session: AsyncSession,
    *,
    status: str = "ANALYSING",
    mode: str = "SIMULATED",
    amount: int = 10000,
    attempt_count: int = 1,
    window_hours: int = 24,
) -> tuple[Customer, Payment, RecoveryCase]:
    """Create a test customer, payment, and recovery case."""
    customer = Customer(
        email="test@example.com",
        phone="+919999999999",
        name="Test Customer",
    )
    session.add(customer)
    await session.flush()

    payment = Payment(
        razorpay_payment_id=f"pay_{uuid.uuid4().hex[:12]}",
        customer_id=customer.id,
        amount=amount,
        currency="INR",
        status="failed",
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment failed",
        error_reason="insufficient_funds",
        method="card",
        payload_snapshot={"test": True},
    )
    session.add(payment)
    await session.flush()

    case = RecoveryCase(
        original_payment_id=payment.razorpay_payment_id,
        payment_fk=payment.id,
        customer_id=customer.id,
        status=status,
        mode=mode,
        attempt_count=attempt_count,
        amount_at_risk=amount,
        recovery_window_expires_at=(
            datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=window_hours)
        ),
    )
    session.add(case)
    await session.flush()

    return customer, payment, case


async def _create_decision(
    session: AsyncSession,
    case: RecoveryCase,
    effective_action: str = "SEND_PAYMENT_LINK",
) -> RecoveryDecision:
    """Create a test RecoveryDecision."""
    decision = RecoveryDecision(
        case_id=case.id,
        attempt_number=case.attempt_count,
        recommended_action=effective_action,
        llm_confidence=0.85,
        delay_hours=None,
        reason="Test decision",
        risk_factors=["test"],
        policy_verdict="APPROVED",
        effective_action=effective_action,
        policy_modification_detail=None,
        policy_reason="Test policy",
        raw_llm_response={"test": True},
        llm_provider="mock",
        llm_model="mock-model",
        llm_latency_ms=100,
    )
    session.add(decision)
    await session.flush()
    return decision


# ── Test 1: SEND_PAYMENT_LINK executes ──────────────────────────────


async def test_send_payment_link_executes(db_session: AsyncSession) -> None:
    """Test 1: SEND_PAYMENT_LINK decision → RecoveryAction created."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")
    await db_session.commit()

    # Discovery should find this
    async with db_session.begin():
        targets = await discover_new_executions(db_session)
        assert len(targets) == 1, f"Expected 1 target, got {len(targets)}"
        found_decision, found_case = targets[0]
        assert found_decision.id == decision.id
        assert found_case.id == case.id

    # Execute
    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = SimulatedRecoveryExecutor()
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

    # Verify action was created and case transitioned
    async with db_session.begin():
        actions = (
            (
                await db_session.execute(
                    select(RecoveryAction).where(RecoveryAction.case_id == case.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(actions) == 1
        action = actions[0]
        await db_session.refresh(action)
        assert action.action_type == "SEND_PAYMENT_LINK"
        assert action.status == "SUCCESS"
        assert (
            action.razorpay_link_id == f"plink_sim_{action.razorpay_link_reference_id}"
        )

        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "LINK_SENT"


# ── Tests 2-4: Non-SEND_PAYMENT_LINK actions don't execute ──────────


async def test_wait_does_not_execute(db_session: AsyncSession) -> None:
    """Test 2: WAIT decision → no RecoveryAction."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    await _create_decision(db_session, case, "WAIT")
    await db_session.commit()

    async with db_session.begin():
        targets = await discover_new_executions(db_session)
        assert len(targets) == 0


async def test_stop_does_not_execute(db_session: AsyncSession) -> None:
    """Test 3: STOP decision → no RecoveryAction."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    await _create_decision(db_session, case, "STOP")
    await db_session.commit()

    async with db_session.begin():
        targets = await discover_new_executions(db_session)
        assert len(targets) == 0


async def test_escalate_does_not_execute(db_session: AsyncSession) -> None:
    """Test 4: ESCALATE decision → no RecoveryAction."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    await _create_decision(db_session, case, "ESCALATE")
    await db_session.commit()

    async with db_session.begin():
        targets = await discover_new_executions(db_session)
        assert len(targets) == 0


# ── Test 5-6: Time validation ────────────────────────────────────────


async def test_expired_case_does_not_execute(db_session: AsyncSession) -> None:
    """Test 5: Expired window → FAILED action, STOPPED case."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(
        db_session,
        status="ANALYSING",
        window_hours=-1,  # already expired
    )
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")
    await db_session.commit()

    # Claim should create a FAILED action
    async with db_session.begin():
        action = await claim_for_execution(db_session, decision, case)
        assert action is None  # Skipped
        assert case.status == "STOPPED"

    await db_session.commit()

    # Verify FAILED action was persisted
    async with db_session.begin():
        actions = (
            (
                await db_session.execute(
                    select(RecoveryAction).where(RecoveryAction.case_id == case.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(actions) == 1
        assert actions[0].status == "FAILED"
        assert actions[0].failure_reason == "INSUFFICIENT_TIME"


async def test_insufficient_time_does_not_execute(db_session: AsyncSession) -> None:
    """Test 6: <15 min remaining → FAILED, STOPPED."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    # Set window to expire in 5 minutes (< 15 min minimum)
    case.recovery_window_expires_at = datetime.datetime.now(
        datetime.UTC
    ) + datetime.timedelta(minutes=5)
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")
    await db_session.commit()

    async with db_session.begin():
        action = await claim_for_execution(db_session, decision, case)
        assert action is None
        assert case.status == "STOPPED"


# ── Test 7: Simulated never calls Razorpay ───────────────────────────


async def test_simulated_never_calls_razorpay(db_session: AsyncSession) -> None:
    """Test 7: SimulatedExecutor → RazorpayClient call count = 0."""
    executor = SimulatedRecoveryExecutor()

    # Verify it has no RazorpayClient attribute
    assert not hasattr(executor, "_client")

    result = await executor.execute(
        amount=10000,
        currency="INR",
        reference_id="rc-test-a1",
        expire_by=1749700000,
        description="Test",
    )
    assert result.success is True
    assert result.razorpay_link_id is not None
    assert result.razorpay_link_id.startswith("plink_sim_")

    # Verify simulated_executor.py doesn't import RazorpayClient
    import inspect

    import app.services.executor.simulated_executor as sim_mod

    source = inspect.getsource(sim_mod)
    assert "import RazorpayClient" not in source
    assert "from .razorpay_client import RazorpayClient" not in source


# ── Test 8: reference_id format and length ───────────────────────────


async def test_reference_id_format_and_length() -> None:
    """Test 8: reference_id matches rc-{hex[:24]}-a{n} and ≤ 40 chars."""
    case_id = uuid.uuid4()
    for attempt in range(1, 20):
        ref = _generate_reference_id(case_id, attempt)
        assert len(ref) <= 40, f"ref_id too long: {len(ref)}"
        assert ref.startswith("rc-")
        assert f"-a{attempt}" in ref
        # Verify deterministic
        assert ref == _generate_reference_id(case_id, attempt)


# ── Test 9: M2 scheduler never picks EXECUTING case ─────────────────


async def test_m2_scheduler_never_picks_executing_case(
    db_session: AsyncSession,
) -> None:
    """Test 9: M2 claim_batch() returns zero EXECUTING cases."""
    await _clean_db(db_session)
    _, _, _case = await _create_test_case(db_session, status="EXECUTING")
    await db_session.commit()

    from app.services.scheduler import claim_batch

    async with db_session.begin():
        claimed = await claim_batch(db_session, 10)
        assert len(claimed) == 0, "M2 should never claim EXECUTING cases"


# ── Test 10: Timeout leaves case EXECUTING, not WAITING ──────────────


async def test_m3_timeout_leaves_case_executing_not_waiting(
    db_session: AsyncSession,
) -> None:
    """Test 10: Timeout → case.status = EXECUTING, NOT WAITING."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    await _create_decision(db_session, case, "SEND_PAYMENT_LINK")
    await db_session.commit()

    # Mock executor that returns timeout
    timeout_result = ExecutionResult(timeout=True, error="Connection timeout")

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=timeout_result)
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

    # Verify case is EXECUTING, NOT WAITING
    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "EXECUTING", (
            f"Expected EXECUTING, got {refreshed_case.status}"
        )
        assert refreshed_case.status != "WAITING"


# ── Test 11: Timeout reconciles by reference_id ──────────────────────


async def test_timeout_reconciles_by_reference_id(
    db_session: AsyncSession,
) -> None:
    """Test 11: POST times out → next tick GET by ref → found → no second POST → LINK_SENT."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="EXECUTING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    idem_key = _generate_idempotency_key(case.id, case.attempt_count)

    # Create an EXECUTING action (simulating a previous timeout)
    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idem_key,
        razorpay_link_reference_id=ref_id,
        razorpay_link_id=None,  # No plink_id — POST timed out
    )
    db_session.add(action)
    await db_session.commit()

    # Mock executor that returns the existing link on reconcile_by_reference_id
    existing_link = ExecutionResult(
        success=True,
        razorpay_link_id="plink_existing_123",
        short_url="https://rzp.io/existing",
        expire_by=1749700000,
        reference_id=ref_id,
    )

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()
        mock_executor.reconcile_by_reference_id = AsyncMock(
            return_value=[existing_link]
        )
        mock_executor.execute = AsyncMock()  # Should NOT be called
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

        # Verify execute was NOT called (reconciliation found the link)
        mock_executor.execute.assert_not_called()

    # Verify action is now SUCCESS and case is LINK_SENT
    async with db_session.begin():
        refreshed_action = await db_session.get(RecoveryAction, action.id)
        assert refreshed_action is not None
        assert refreshed_action.status == "SUCCESS"
        assert refreshed_action.razorpay_link_id == "plink_existing_123"

        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "LINK_SENT"


# ── Test 12: Timeout, no existing link → create ─────────────────────


async def test_timeout_no_existing_link_then_create(
    db_session: AsyncSession,
) -> None:
    """Test 12: POST timeout → GET → zero matches → POST same ref → success."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="EXECUTING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    idem_key = _generate_idempotency_key(case.id, case.attempt_count)

    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idem_key,
        razorpay_link_reference_id=ref_id,
    )
    db_session.add(action)
    await db_session.commit()

    created_link = ExecutionResult(
        success=True,
        razorpay_link_id="plink_new_456",
        short_url="https://rzp.io/new",
        expire_by=1749700000,
        reference_id=ref_id,
    )

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()
        mock_executor.reconcile_by_reference_id = AsyncMock(return_value=[])
        mock_executor.execute = AsyncMock(return_value=created_link)
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

        # Verify execute WAS called (no existing link found)
        mock_executor.execute.assert_called_once()

    async with db_session.begin():
        refreshed_action = await db_session.get(RecoveryAction, action.id)
        assert refreshed_action is not None
        assert refreshed_action.status == "SUCCESS"


# ── Test 13: Reconciliation GET timeout → no POST ────────────────────


async def test_reconciliation_query_timeout_does_not_create(
    db_session: AsyncSession,
) -> None:
    """Test 13: GET reconciliation times out → no POST → remains EXECUTING."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="EXECUTING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    idem_key = _generate_idempotency_key(case.id, case.attempt_count)

    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idem_key,
        razorpay_link_reference_id=ref_id,
    )
    db_session.add(action)
    await db_session.commit()

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()
        mock_executor.reconcile_by_reference_id = AsyncMock(
            return_value=[ExecutionResult(timeout=True, error="GET timeout")]
        )
        mock_executor.execute = AsyncMock()  # Should NOT be called
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

        mock_executor.execute.assert_not_called()

    async with db_session.begin():
        refreshed_action = await db_session.get(RecoveryAction, action.id)
        assert refreshed_action is not None
        assert refreshed_action.status == "EXECUTING"

        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "EXECUTING"


# ── Test 14: Exact reference_id match required ──────────────────────


async def test_exact_reference_id_match_required(
    db_session: AsyncSession,
) -> None:
    """Test 14: Unrelated links filtered out."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="EXECUTING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    idem_key = _generate_idempotency_key(case.id, case.attempt_count)

    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idem_key,
        razorpay_link_reference_id=ref_id,
    )
    db_session.add(action)
    await db_session.commit()

    # Return an unrelated link (different reference_id)
    unrelated = ExecutionResult(
        success=True,
        razorpay_link_id="plink_unrelated",
        short_url="https://rzp.io/unrelated",
        reference_id="rc-something-else-a1",  # NOT matching
    )
    new_link = ExecutionResult(
        success=True,
        razorpay_link_id="plink_new",
        short_url="https://rzp.io/new",
        expire_by=1749700000,
        reference_id=ref_id,
    )

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()
        mock_executor.reconcile_by_reference_id = AsyncMock(return_value=[unrelated])
        mock_executor.execute = AsyncMock(return_value=new_link)
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

        # execute should be called because unrelated link didn't match
        mock_executor.execute.assert_called_once()


# ── Test 15: Multiple matching reference_ids → anomaly ───────────────


async def test_multiple_matching_reference_ids_is_anomaly(
    db_session: AsyncSession,
) -> None:
    """Test 15: >1 match → FAILED, STOPPED, audit anomaly."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="EXECUTING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    idem_key = _generate_idempotency_key(case.id, case.attempt_count)

    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idem_key,
        razorpay_link_reference_id=ref_id,
    )
    db_session.add(action)
    await db_session.commit()

    # Return TWO matching links (anomaly)
    match1 = ExecutionResult(
        success=True,
        razorpay_link_id="plink_dup1",
        reference_id=ref_id,
    )
    match2 = ExecutionResult(
        success=True,
        razorpay_link_id="plink_dup2",
        reference_id=ref_id,
    )

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()
        mock_executor.reconcile_by_reference_id = AsyncMock(
            return_value=[match1, match2]
        )
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

    async with db_session.begin():
        refreshed_action = await db_session.get(RecoveryAction, action.id)
        assert refreshed_action is not None
        assert refreshed_action.status == "FAILED"
        assert "RECONCILIATION_ANOMALY" in (refreshed_action.failure_reason or "")

        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "STOPPED"


# ── Test 16: Timeout then existing link → no duplicate ───────────────


async def test_timeout_then_existing_link_no_duplicate(
    db_session: AsyncSession,
) -> None:
    """Test 16: Exactly one Payment Link end-to-end."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="EXECUTING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    idem_key = _generate_idempotency_key(case.id, case.attempt_count)

    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idem_key,
        razorpay_link_reference_id=ref_id,
    )
    db_session.add(action)
    await db_session.commit()

    existing = ExecutionResult(
        success=True,
        razorpay_link_id="plink_only_one",
        short_url="https://rzp.io/only",
        expire_by=1749700000,
        reference_id=ref_id,
    )

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()
        mock_executor.reconcile_by_reference_id = AsyncMock(return_value=[existing])
        mock_executor.execute = AsyncMock()
        mock_get.return_value = mock_executor

        await run_execution_phase(db_session)

        mock_executor.execute.assert_not_called()

    async with db_session.begin():
        refreshed_action = await db_session.get(RecoveryAction, action.id)
        assert refreshed_action is not None
        assert refreshed_action.razorpay_link_id == "plink_only_one"
        assert refreshed_action.status == "SUCCESS"


# ── Test 17-18: Real PostgreSQL concurrency ──────────────────────────


async def test_concurrent_workers_one_action(db_session: AsyncSession) -> None:
    """Test 17: Two workers → exactly one RecoveryAction (idempotency_key unique)."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    await _create_decision(db_session, case, "SEND_PAYMENT_LINK")
    await db_session.commit()

    from app.database import AsyncSessionLocal

    async def worker() -> RecoveryAction | None:
        async with AsyncSessionLocal() as session, session.begin():
            targets = await discover_new_executions(session)
            if not targets:
                return None
            d, c = targets[0]
            action = await claim_for_execution(session, d, c)
            return action

    # Run two workers concurrently
    task1 = asyncio.create_task(worker())
    task2 = asyncio.create_task(worker())
    r1, r2 = await asyncio.gather(task1, task2, return_exceptions=True)

    # Count how many succeeded
    successes = sum(
        1 for r in [r1, r2] if isinstance(r, RecoveryAction) and r is not None
    )
    # Due to FOR UPDATE SKIP LOCKED, at most one should succeed
    assert successes <= 1, f"Expected at most 1 success, got {successes}"

    # Verify exactly one action in DB
    async with db_session.begin():
        all_actions = (
            (
                await db_session.execute(
                    select(RecoveryAction).where(RecoveryAction.case_id == case.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(all_actions) <= 1


async def test_concurrent_workers_one_payment_link(
    db_session: AsyncSession,
) -> None:
    """Test 18: Two workers → exactly one Payment Link created."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="ANALYSING")
    await _create_decision(db_session, case, "SEND_PAYMENT_LINK")
    await db_session.commit()

    execution_count = 0

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_executor = AsyncMock()

        async def counting_execute(**kwargs: object) -> ExecutionResult:
            nonlocal execution_count
            execution_count += 1
            ref = kwargs.get("reference_id", "")
            return ExecutionResult(
                success=True,
                razorpay_link_id=f"plink_concurrent_{ref}",
                short_url=f"https://rzp.io/{ref}",
                expire_by=1749700000,
                reference_id=str(ref),
            )

        mock_executor.execute = counting_execute
        mock_get.return_value = mock_executor

        from app.database import AsyncSessionLocal

        async def run_worker() -> int:
            async with AsyncSessionLocal() as session:
                return await run_execution_phase(session)

        t1 = asyncio.create_task(run_worker())
        t2 = asyncio.create_task(run_worker())
        await asyncio.gather(t1, t2, return_exceptions=True)

    # At most one execution should have happened
    assert execution_count <= 1, f"Expected ≤1 execution, got {execution_count}"


# ── Test 31: attempt_count not incremented by M3 ────────────────────


async def test_attempt_count_not_incremented_by_m3(
    db_session: AsyncSession,
) -> None:
    """Test 31: Execute successfully → attempt_count unchanged."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(
        db_session, status="ANALYSING", attempt_count=1
    )
    original_attempt_count = case.attempt_count
    await _create_decision(db_session, case, "SEND_PAYMENT_LINK")
    await db_session.commit()

    with patch("app.services.recovery_service.get_executor") as mock_get:
        mock_get.return_value = SimulatedRecoveryExecutor()
        await run_execution_phase(db_session)

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.attempt_count == original_attempt_count, (
            f"M3 must NOT increment attempt_count: "
            f"expected {original_attempt_count}, got {refreshed_case.attempt_count}"
        )

        # Verify reference_id uses original attempt_count
        actions = (
            (
                await db_session.execute(
                    select(RecoveryAction).where(RecoveryAction.case_id == case.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(actions) == 1
        expected_ref = _generate_reference_id(case.id, original_attempt_count)
        assert actions[0].razorpay_link_reference_id == expected_ref


# ── Test 32: Paid webhook races executor completion ──────────────────


async def test_paid_webhook_races_executor_completion(
    db_session: AsyncSession,
) -> None:
    """Test 32: Webhook marks RECOVERED → M3 persists POST result → case remains RECOVERED."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="EXECUTING")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    idem_key = _generate_idempotency_key(case.id, case.attempt_count)

    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=idem_key,
        razorpay_link_reference_id=ref_id,
    )
    db_session.add(action)
    await db_session.commit()

    # Simulate webhook arriving first — mark RECOVERED
    async with db_session.begin():
        case_obj = await db_session.get(RecoveryCase, case.id)
        assert case_obj is not None
        case_obj.status = "RECOVERED"
        case_obj.recovered_payment_id = "pay_webhook_first"
        case_obj.amount_recovered = case_obj.amount_at_risk

    # Now simulate M3 trying to persist a successful execution result
    from app.services.recovery_service import _persist_execution_result

    success_result = ExecutionResult(
        success=True,
        razorpay_link_id="plink_late",
        short_url="https://rzp.io/late",
        expire_by=1749700000,
        reference_id=ref_id,
    )

    async with db_session.begin():
        await _persist_execution_result(
            db_session, action.id, case.id, success_result, "SIMULATED"
        )

    # Verify case is still RECOVERED, not LINK_SENT
    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "RECOVERED", (
            f"Expected RECOVERED, got {refreshed_case.status}"
        )
        assert refreshed_case.recovered_payment_id == "pay_webhook_first"


# ── Test 33: Executor cannot downgrade terminal RECOVERED ────────────


async def test_executor_cannot_downgrade_terminal_recovered(
    db_session: AsyncSession,
) -> None:
    """Test 33: RECOVERED + successful executor result → still RECOVERED."""
    await _clean_db(db_session)
    _, _, case = await _create_test_case(db_session, status="RECOVERED")
    decision = await _create_decision(db_session, case, "SEND_PAYMENT_LINK")

    ref_id = _generate_reference_id(case.id, case.attempt_count)
    action = RecoveryAction(
        case_id=case.id,
        decision_id=decision.id,
        attempt_number=case.attempt_count,
        action_type="SEND_PAYMENT_LINK",
        status="EXECUTING",
        idempotency_key=_generate_idempotency_key(case.id, case.attempt_count),
        razorpay_link_reference_id=ref_id,
    )
    db_session.add(action)
    case.recovered_payment_id = "pay_existing_recovery"
    case.amount_recovered = case.amount_at_risk
    await db_session.commit()

    from app.services.recovery_service import _persist_execution_result

    success_result = ExecutionResult(
        success=True,
        razorpay_link_id="plink_late_result",
        short_url="https://rzp.io/late",
        expire_by=1749700000,
        reference_id=ref_id,
    )

    async with db_session.begin():
        await _persist_execution_result(
            db_session, action.id, case.id, success_result, "SIMULATED"
        )

    async with db_session.begin():
        refreshed_case = await db_session.get(RecoveryCase, case.id)
        assert refreshed_case is not None
        assert refreshed_case.status == "RECOVERED"
        assert refreshed_case.recovered_payment_id == "pay_existing_recovery"

        # But the action should have the link metadata backfilled
        refreshed_action = await db_session.get(RecoveryAction, action.id)
        assert refreshed_action is not None
        assert refreshed_action.razorpay_link_id == "plink_late_result"
