"""Comprehensive simulation tests — determinism, clock isolation, idempotency,
partial-run recovery, heuristic outcome model, and fail-closed reconciliation.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.models import (
    AuditEvent,
    Payment,
    RecoveryAction,
    RecoveryCase,
    RecoveryDecision,
)
from app.services.safety_validator import compute_heuristic_likelihood
from app.services.simulation_service import (
    SIM_EPOCH,
    generate_scenario_data,
    get_deterministic_hash,
    run_simulation,
)


@pytest.fixture(autouse=True)
async def clean_db_before_test(db_session):
    """Ensure clean slate for each simulation test."""
    await db_session.execute(delete(AuditEvent))
    await db_session.execute(delete(RecoveryAction))
    await db_session.execute(delete(RecoveryDecision))
    await db_session.execute(delete(RecoveryCase))
    await db_session.execute(delete(Payment))
    await db_session.commit()


# ---------------------------------------------------------------------------
# 1. SHA-256 Determinism
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sha256_determinism():
    """SHA-256 hash is stable across calls and yields values in [0, 1]."""
    hash1 = get_deterministic_hash(42, 0)
    hash2 = get_deterministic_hash(42, 0)
    hash3 = get_deterministic_hash(42, 1)

    assert hash1 == hash2
    assert hash1 != hash3
    assert 0.0 <= hash1 <= 1.0


# ---------------------------------------------------------------------------
# 2. Clock isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulation_clock_isolation(db_session):
    """Simulation timestamps are anchored to SIM_EPOCH, not real time."""
    await run_simulation(db_session, seed=123, scenario_count=1)

    case = (
        (
            await db_session.execute(
                select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED")
            )
        )
        .scalars()
        .first()
    )
    assert case is not None

    payment = (
        (
            await db_session.execute(
                select(Payment).where(
                    Payment.razorpay_payment_id == case.original_payment_id
                )
            )
        )
        .scalars()
        .first()
    )
    assert payment is not None

    # Verify time is anchored to SIM_EPOCH, not real time
    assert payment.failed_at < SIM_EPOCH
    assert payment.failed_at.year == 2023 or payment.failed_at.year == 2024

    if case.resolved_at:
        assert case.resolved_at == SIM_EPOCH


# ---------------------------------------------------------------------------
# 3. Idempotency — zero duplicates on rerun
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulation_idempotency_zero_duplicates(db_session):
    """Same seed/count rerun = zero duplicates."""
    res1 = await run_simulation(db_session, seed=456, scenario_count=3)
    assert res1["new_cases_processed"] == 3
    assert res1["already_existed"] == 0

    # Rerun
    res2 = await run_simulation(db_session, seed=456, scenario_count=3)
    assert res2["new_cases_processed"] == 0
    assert res2["already_existed"] == 3

    # Verify counts in DB
    cases = (
        (
            await db_session.execute(
                select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED")
            )
        )
        .scalars()
        .all()
    )
    assert len(cases) == 3


# ---------------------------------------------------------------------------
# 4. Scenario expansion preserves prior indices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_expansion_preserves_identity(db_session):
    """Same seed, different scenario_count preserves prior indices."""
    await run_simulation(db_session, seed=789, scenario_count=2)

    cases_before = (
        (
            await db_session.execute(
                select(RecoveryCase)
                .where(RecoveryCase.mode == "SIMULATED")
                .order_by(RecoveryCase.original_payment_id)
            )
        )
        .scalars()
        .all()
    )

    res2 = await run_simulation(db_session, seed=789, scenario_count=4)
    assert res2["new_cases_processed"] == 2
    assert res2["already_existed"] == 2

    cases_after = (
        (
            await db_session.execute(
                select(RecoveryCase)
                .where(RecoveryCase.mode == "SIMULATED")
                .order_by(RecoveryCase.original_payment_id)
            )
        )
        .scalars()
        .all()
    )

    assert cases_before[0].original_payment_id == cases_after[0].original_payment_id
    assert cases_before[1].original_payment_id == cases_after[1].original_payment_id


# ---------------------------------------------------------------------------
# 5. Simulated plink IDs populated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulated_plink_ids_populated(db_session):
    """Synthetic plink IDs are populated on actions."""
    await run_simulation(db_session, seed=999, scenario_count=5)

    actions = (
        (
            await db_session.execute(
                select(RecoveryAction).where(
                    RecoveryAction.action_type == "SEND_PAYMENT_LINK"
                )
            )
        )
        .scalars()
        .all()
    )
    for action in actions:
        assert action.status == "SUCCESS"
        assert action.razorpay_link_id.startswith("plink_sim_")


# ---------------------------------------------------------------------------
# 6. Heuristic-based outcome model (NOT flat 30%)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heuristic_outcome_model_used(db_session):
    """Simulation outcome uses compute_heuristic_likelihood, not flat 0.30."""
    await run_simulation(db_session, seed=7777, scenario_count=50)

    cases = (
        (
            await db_session.execute(
                select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED")
            )
        )
        .scalars()
        .all()
    )

    # Check that for each case, the outcome is consistent with the heuristic
    for idx in range(50):
        scenario = generate_scenario_data(7777, idx)
        likelihood = compute_heuristic_likelihood(scenario["reason"])
        outcome_h = get_deterministic_hash(7777, idx + 40000)
        expected_recovered = outcome_h < likelihood

        payment_id = f"pay_sim_src_7777_{idx}"
        case = next((c for c in cases if c.original_payment_id == payment_id), None)
        if case and case.status in ["RECOVERED", "STOPPED"]:
            if expected_recovered:
                assert case.status == "RECOVERED", (
                    f"Scenario {idx}: expected RECOVERED (h={outcome_h:.3f} < likelihood={likelihood:.3f})"
                )
            else:
                assert case.status == "STOPPED", (
                    f"Scenario {idx}: expected STOPPED (h={outcome_h:.3f} >= likelihood={likelihood:.3f})"
                )


# ---------------------------------------------------------------------------
# 7. Recovered payment idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovered_payment_idempotency(db_session):
    """Recovered payments have correct IDs and captured payments match count."""
    await run_simulation(db_session, seed=101, scenario_count=10)

    rec_cases = (
        (
            await db_session.execute(
                select(RecoveryCase).where(RecoveryCase.status == "RECOVERED")
            )
        )
        .scalars()
        .all()
    )

    payments = (
        (await db_session.execute(select(Payment).where(Payment.status == "captured")))
        .scalars()
        .all()
    )

    assert len(rec_cases) == len(payments)

    for p in payments:
        assert p.razorpay_payment_id.startswith("pay_sim_rec_101_")


# ---------------------------------------------------------------------------
# 8. No invented case states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_invented_case_states(db_session):
    """No EXPIRED or other invented states exist."""
    await run_simulation(db_session, seed=202, scenario_count=20)

    cases = (await db_session.execute(select(RecoveryCase))).scalars().all()
    valid_states = {
        "CREATED",
        "ANALYSING",
        "WAITING",
        "EXECUTING",
        "LINK_SENT",
        "RECOVERED",
        "STOPPED",
        "ESCALATED",
    }
    for case in cases:
        assert case.status in valid_states


# ---------------------------------------------------------------------------
# 9. Partial-run recovery — ANALYSING state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_run_analysing(db_session):
    """Case stuck in ANALYSING (crash during M2) resumes safely."""
    res1 = await run_simulation(db_session, seed=303, scenario_count=1)
    assert res1["new_cases_processed"] == 1

    case = (await db_session.execute(select(RecoveryCase))).scalars().first()

    # Mutate to ANALYSING (simulate a crash during M2)
    case.status = "ANALYSING"
    await db_session.commit()

    res2 = await run_simulation(db_session, seed=303, scenario_count=1)
    assert res2 is not None

    case = (await db_session.execute(select(RecoveryCase))).scalars().first()
    assert case.status in ["RECOVERED", "STOPPED"]


# ---------------------------------------------------------------------------
# 10. Partial-run recovery — EXECUTING with existing action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_run_executing_with_action(db_session):
    """Case stuck in EXECUTING with existing action resumes without duplicate action."""
    await run_simulation(db_session, seed=404, scenario_count=1)

    case = (await db_session.execute(select(RecoveryCase))).scalars().first()
    actions_before = (
        (
            await db_session.execute(
                select(RecoveryAction).where(RecoveryAction.case_id == case.id)
            )
        )
        .scalars()
        .all()
    )

    # Mutate to EXECUTING (simulate crash after claim_for_execution)
    case.status = "EXECUTING"
    for a in actions_before:
        a.status = "EXECUTING"
    await db_session.commit()

    # Rerun — should reconcile, not create duplicate
    await run_simulation(db_session, seed=404, scenario_count=1)

    actions_after = (
        (
            await db_session.execute(
                select(RecoveryAction).where(RecoveryAction.case_id == case.id)
            )
        )
        .scalars()
        .all()
    )
    # Should not have created additional actions
    assert len(actions_after) == len(actions_before)


# ---------------------------------------------------------------------------
# 11. Partial-run recovery — LINK_SENT with successful action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_run_link_sent_with_action(db_session):
    """Case in LINK_SENT with successful action resumes outcome phase only."""
    await run_simulation(db_session, seed=505, scenario_count=1)

    case = (await db_session.execute(select(RecoveryCase))).scalars().first()

    if case.status in ["RECOVERED", "STOPPED"]:
        # Reset to LINK_SENT to simulate partial run
        case.status = "LINK_SENT"
        case.amount_recovered = 0
        case.recovered_payment_id = None
        case.resolved_at = None
        # Remove the recovered payment if any
        rec_pay = (
            (
                await db_session.execute(
                    select(Payment).where(
                        Payment.razorpay_payment_id.like("pay_sim_rec_%")
                    )
                )
            )
            .scalars()
            .first()
        )
        if rec_pay:
            await db_session.delete(rec_pay)
        # Reset action outcome
        action = (
            (
                await db_session.execute(
                    select(RecoveryAction).where(RecoveryAction.case_id == case.id)
                )
            )
            .scalars()
            .first()
        )
        if action:
            action.outcome = None
        await db_session.commit()

        # Rerun
        await run_simulation(db_session, seed=505, scenario_count=1)

        await db_session.refresh(case)
        assert case.status in ["RECOVERED", "STOPPED"]


# ---------------------------------------------------------------------------
# 12. Partial-run recovery — LINK_SENT with existing recovered payment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_run_link_sent_with_recovered_payment(db_session):
    """Case in LINK_SENT with existing recovered payment reconciles to RECOVERED."""
    await run_simulation(db_session, seed=606, scenario_count=1)

    case = (await db_session.execute(select(RecoveryCase))).scalars().first()
    scenario = generate_scenario_data(606, 0)
    likelihood = compute_heuristic_likelihood(scenario["reason"])
    outcome_h = get_deterministic_hash(606, 40000)

    if outcome_h < likelihood:
        # This scenario should recover. Reset case to LINK_SENT to test reconciliation
        recovered_payment_id = "pay_sim_rec_606_0"
        rec_pay = (
            (
                await db_session.execute(
                    select(Payment).where(
                        Payment.razorpay_payment_id == recovered_payment_id
                    )
                )
            )
            .scalars()
            .first()
        )

        if rec_pay:
            # Reset case but keep the recovered payment
            case.status = "LINK_SENT"
            case.amount_recovered = 0
            case.recovered_payment_id = None
            case.resolved_at = None
            action = (
                (
                    await db_session.execute(
                        select(RecoveryAction).where(RecoveryAction.case_id == case.id)
                    )
                )
                .scalars()
                .first()
            )
            if action:
                action.outcome = None
            await db_session.commit()

            # Rerun — should reconcile existing payment, not create duplicate
            await run_simulation(db_session, seed=606, scenario_count=1)

            await db_session.refresh(case)
            assert case.status == "RECOVERED"
            assert case.amount_recovered == case.amount_at_risk
            assert case.recovered_payment_id == recovered_payment_id

            # Verify no duplicate payment was created
            rec_payments = (
                (
                    await db_session.execute(
                        select(Payment).where(
                            Payment.razorpay_payment_id == recovered_payment_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rec_payments) == 1


# ---------------------------------------------------------------------------
# 13. Fail-closed reconciliation on inconsistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovered_payment_fail_closed_on_inconsistency(db_session):
    """Reconciliation fails safely when recovered payment has wrong amount."""
    await run_simulation(db_session, seed=707, scenario_count=1)

    case = (await db_session.execute(select(RecoveryCase))).scalars().first()
    scenario = generate_scenario_data(707, 0)
    likelihood = compute_heuristic_likelihood(scenario["reason"])
    outcome_h = get_deterministic_hash(707, 40000)

    if outcome_h < likelihood:
        # This scenario recovers. Tamper with the recovered payment and reset case
        recovered_payment_id = "pay_sim_rec_707_0"
        rec_pay = (
            (
                await db_session.execute(
                    select(Payment).where(
                        Payment.razorpay_payment_id == recovered_payment_id
                    )
                )
            )
            .scalars()
            .first()
        )

        if rec_pay:
            # Tamper with amount
            rec_pay.amount = 999999999
            case.status = "LINK_SENT"
            case.amount_recovered = 0
            case.recovered_payment_id = None
            case.resolved_at = None
            action = (
                (
                    await db_session.execute(
                        select(RecoveryAction).where(RecoveryAction.case_id == case.id)
                    )
                )
                .scalars()
                .first()
            )
            if action:
                action.outcome = None
            await db_session.commit()

            # Rerun — should STOP with inconsistency, not RECOVERED
            await run_simulation(db_session, seed=707, scenario_count=1)

            await db_session.refresh(case)
            assert case.status == "STOPPED"
            assert "RECONCILIATION_INCONSISTENCY" in (case.stop_reason or "")


# ---------------------------------------------------------------------------
# 14. Determinism — identical results from two isolated runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_determinism_across_runs(db_session):
    """Same seed + scenario_count in isolated clean datasets produces identical results."""
    seed = 808
    count = 10

    # --- Helper to collect deterministic fingerprint ---
    async def collect_fingerprint(session):
        cases = (
            (
                await session.execute(
                    select(RecoveryCase)
                    .where(RecoveryCase.mode == "SIMULATED")
                    .order_by(RecoveryCase.original_payment_id)
                )
            )
            .scalars()
            .all()
        )

        # Build case_id → original_payment_id map for stable sorting
        case_map = {c.id: c.original_payment_id for c in cases}

        case_data = [
            (
                c.original_payment_id,
                c.status,
                c.amount_recovered,
                c.recovered_payment_id,
            )
            for c in cases
        ]

        all_actions = (await session.execute(select(RecoveryAction))).scalars().all()
        action_data = sorted(
            [
                (
                    case_map.get(a.case_id, ""),
                    a.attempt_number,
                    a.action_type,
                    a.status,
                    a.outcome,
                )
                for a in all_actions
            ]
        )

        all_decisions = (
            (await session.execute(select(RecoveryDecision))).scalars().all()
        )
        decision_data = sorted(
            [
                (
                    case_map.get(d.case_id, ""),
                    d.attempt_number,
                    d.effective_action,
                    d.policy_verdict,
                )
                for d in all_decisions
            ]
        )

        return case_data, action_data, decision_data

    # Run 1
    await run_simulation(db_session, seed=seed, scenario_count=count)
    fp1 = await collect_fingerprint(db_session)

    # Clean everything
    await db_session.execute(delete(AuditEvent))
    await db_session.execute(delete(RecoveryAction))
    await db_session.execute(delete(RecoveryDecision))
    await db_session.execute(delete(RecoveryCase))
    await db_session.execute(delete(Payment))
    await db_session.commit()

    from app.models.customer import Customer

    await db_session.execute(
        delete(Customer).where(Customer.name.like("Sim Customer%"))
    )
    await db_session.commit()

    # Run 2 — identical inputs
    await run_simulation(db_session, seed=seed, scenario_count=count)
    fp2 = await collect_fingerprint(db_session)

    # Assert identical fingerprints
    assert fp1[0] == fp2[0], f"Case data mismatch:\n{fp1[0]}\nvs\n{fp2[0]}"
    assert fp1[1] == fp2[1], f"Action data mismatch:\n{fp1[1]}\nvs\n{fp2[1]}"
    assert fp1[2] == fp2[2], f"Decision data mismatch:\n{fp1[2]}\nvs\n{fp2[2]}"


# ---------------------------------------------------------------------------
# 15. Wall-clock independence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wall_clock_independence(db_session):
    """Changing real wall-clock time does not affect simulation results.

    We prove this by verifying that no simulation timestamp depends on
    datetime.now() — all simulation data points are anchored to SIM_EPOCH.
    """
    await run_simulation(db_session, seed=909, scenario_count=5)

    cases = (
        (
            await db_session.execute(
                select(RecoveryCase)
                .where(RecoveryCase.mode == "SIMULATED")
                .order_by(RecoveryCase.original_payment_id)
            )
        )
        .scalars()
        .all()
    )

    now_real = datetime.now(UTC)

    for case in cases:
        # Case timestamps must be anchored to SIM_EPOCH, not real time
        if case.resolved_at:
            assert case.resolved_at == SIM_EPOCH
            assert case.resolved_at.year == 2024
            assert abs((case.resolved_at - now_real).total_seconds()) > 3600

        # Recovery window must be based on failed_at, not wall clock
        payment = (
            (
                await db_session.execute(
                    select(Payment).where(
                        Payment.razorpay_payment_id == case.original_payment_id
                    )
                )
            )
            .scalars()
            .first()
        )
        assert payment.failed_at.year in (2023, 2024)
        assert abs((payment.failed_at - now_real).total_seconds()) > 3600

    # Verify actions have no wall-clock timestamps
    actions = (
        (
            await db_session.execute(
                select(RecoveryAction).where(
                    RecoveryAction.action_type == "SEND_PAYMENT_LINK"
                )
            )
        )
        .scalars()
        .all()
    )
    for action in actions:
        if action.executed_at:
            assert action.executed_at.year == 2024
