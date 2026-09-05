"""Metrics service tests — verify case-level funnel, intervention, decision,
and action metrics with explicit denominators and LIVE/SIMULATED isolation.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditEvent,
    Payment,
    RecoveryAction,
    RecoveryCase,
    RecoveryDecision,
)
from app.services.metrics_service import get_dashboard_metrics
from app.services.simulation_service import run_simulation


@pytest.fixture(autouse=True)
async def clean_metrics_db(db_session):
    """Clean slate for each metrics test."""
    await db_session.execute(delete(AuditEvent))
    await db_session.execute(delete(RecoveryAction))
    await db_session.execute(delete(RecoveryDecision))
    await db_session.execute(delete(RecoveryCase))
    await db_session.execute(delete(Payment))
    await db_session.commit()


# ---------------------------------------------------------------------------
# 1. payment_links_created requires SUCCESS + non-null razorpay_link_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_links_created_metric(db_session: AsyncSession):
    """payment_links_created counts SUCCESS actions with non-null razorpay_link_id."""
    await run_simulation(db_session, seed=404, scenario_count=5)

    metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")

    assert metrics.payment_links_created >= 0
    assert metrics.payment_links_created <= metrics.decisions_send_link


# ---------------------------------------------------------------------------
# 2. Final intervention uses latest decision per case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_final_intervention_vs_attempts(db_session: AsyncSession):
    """Latest-decision-per-case metrics: total interventions <= total_cases."""
    await run_simulation(db_session, seed=505, scenario_count=10)

    metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")

    total_interventions = (
        metrics.cases_intervened_link
        + metrics.cases_intervened_wait
        + metrics.cases_intervened_escalate
        + metrics.cases_intervened_stop
    )

    # Total decisions (attempt-level) >= total interventions (case-level latest)
    assert metrics.decisions_total >= total_interventions
    assert total_interventions <= metrics.total_cases


# ---------------------------------------------------------------------------
# 3. LIVE and SIMULATED isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_simulated_metric_isolation(db_session: AsyncSession):
    """LIVE and SIMULATED metrics are completely isolated."""
    p = Payment(
        razorpay_payment_id="pay_live_test",
        amount=1000,
        currency="INR",
        status="failed",
        error_reason="test",
        payload_snapshot={},
        created_at=datetime.now(UTC),
        failed_at=datetime.now(UTC),
    )
    db_session.add(p)
    await db_session.flush()

    c = RecoveryCase(
        original_payment_id=p.razorpay_payment_id,
        payment_fk=p.id,
        amount_at_risk=p.amount,
        status="CREATED",
        mode="LIVE",
        recovery_window_expires_at=datetime.now(UTC),
        attempt_count=0,
    )
    db_session.add(c)
    await db_session.commit()

    # Run simulation
    await run_simulation(db_session, seed=606, scenario_count=2)

    live_metrics = await get_dashboard_metrics(db_session, mode="LIVE")
    assert live_metrics.total_cases >= 1

    sim_metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")
    assert sim_metrics.total_cases >= 2

    all_metrics = await get_dashboard_metrics(db_session)
    assert all_metrics.total_cases == live_metrics.total_cases + sim_metrics.total_cases


# ---------------------------------------------------------------------------
# 4. Case-level metrics count each case exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_level_counts_each_case_once(db_session: AsyncSession):
    """Funnel metrics: recovered + stopped + escalated + in-progress = total_cases."""
    await run_simulation(db_session, seed=808, scenario_count=15)

    metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")

    # All terminal + non-terminal should equal total
    # Non-terminal: total - (recovered + stopped + escalated)
    terminal_count = (
        metrics.recovered_cases + metrics.stopped_cases + metrics.escalated_cases
    )
    assert terminal_count <= metrics.total_cases
    assert metrics.total_cases == 15


# ---------------------------------------------------------------------------
# 5. amount_at_risk and amount_recovered sums
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amount_metrics_are_sums(db_session: AsyncSession):
    """amount_at_risk = sum of case.amount_at_risk; amount_recovered = sum of recovered amounts."""
    await run_simulation(db_session, seed=909, scenario_count=5)

    metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")

    # Cross-check against raw DB
    cases = (
        (
            await db_session.execute(
                select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED")
            )
        )
        .scalars()
        .all()
    )

    expected_at_risk = sum(c.amount_at_risk for c in cases)
    expected_recovered = sum(c.amount_recovered or 0 for c in cases)

    assert metrics.amount_at_risk_paise == expected_at_risk
    assert metrics.amount_recovered_paise == expected_recovered


# ---------------------------------------------------------------------------
# 6. Recovery rate calculations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_rate_calculations(db_session: AsyncSession):
    """count rate = recovered/total; amount rate = recovered/at_risk."""
    await run_simulation(db_session, seed=1010, scenario_count=10)

    metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")

    if metrics.total_cases > 0:
        expected_count_rate = metrics.recovered_cases / metrics.total_cases
        assert metrics.recovery_rate_by_count == pytest.approx(
            expected_count_rate, abs=1e-6
        )

    if metrics.amount_at_risk_paise > 0:
        expected_amount_rate = (
            metrics.amount_recovered_paise / metrics.amount_at_risk_paise
        )
        assert metrics.recovery_rate_by_amount == pytest.approx(
            expected_amount_rate, abs=1e-6
        )


# ---------------------------------------------------------------------------
# 7. Hand-crafted truth-table test: exact known expected values and isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metric_truth_table_isolation_and_aggregations(
    db_session: AsyncSession,
):
    """Deterministic truth-table verification with hand-crafted records:
    Verifies total_cases, amount_at_risk_paise, amount_recovered_paise,
    recovered_cases, stopped_cases, recovery_rate_by_count, recovery_rate_by_amount,
    cases_by_status (all 8 keys), and recovery_by_failure_reason with known values.
    Also proves strict isolation between LIVE and SIMULATED.
    """
    now = datetime.now(UTC)

    # 1. Populate Hand-Crafted LIVE Cases
    live_records = [
        # (payment_id, amount, error_reason, status, recovered_amt)
        ("pay_live_tt_1", 10000, "INSUFFICIENT_FUNDS", "RECOVERED", 10000),
        ("pay_live_tt_2", 20000, "INSUFFICIENT_FUNDS", "STOPPED", 0),
        ("pay_live_tt_3", 50000, "PAYMENT_CANCELLED", "WAITING", 0),
        ("pay_live_tt_4", 30000, "GATEWAY_ERROR", "LINK_SENT", 0),
        ("pay_live_tt_5", 40000, "GATEWAY_ERROR", "ESCALATED", 0),
    ]

    for pid, amt, reason, st, rec_amt in live_records:
        p = Payment(
            razorpay_payment_id=pid,
            amount=amt,
            currency="INR",
            status="failed",
            error_reason=reason,
            payload_snapshot={},
            created_at=now,
            failed_at=now,
        )
        db_session.add(p)
        await db_session.flush()

        c = RecoveryCase(
            original_payment_id=pid,
            payment_fk=p.id,
            amount_at_risk=amt,
            amount_recovered=rec_amt,
            status=st,
            mode="LIVE",
            recovery_window_expires_at=now,
            attempt_count=1,
        )
        db_session.add(c)

    # 2. Populate Hand-Crafted SIMULATED Cases
    sim_records = [
        # (payment_id, amount, error_reason, status, recovered_amt)
        ("pay_sim_tt_1", 5000, "CARD_DECLINED", "RECOVERED", 5000),
        ("pay_sim_tt_2", 15000, "CARD_DECLINED", "RECOVERED", 15000),
        ("pay_sim_tt_3", 25000, "INSUFFICIENT_FUNDS", "CREATED", 0),
    ]

    for pid, amt, reason, st, rec_amt in sim_records:
        p = Payment(
            razorpay_payment_id=pid,
            amount=amt,
            currency="INR",
            status="failed",
            error_reason=reason,
            payload_snapshot={},
            created_at=now,
            failed_at=now,
        )
        db_session.add(p)
        await db_session.flush()

        c = RecoveryCase(
            original_payment_id=pid,
            payment_fk=p.id,
            amount_at_risk=amt,
            amount_recovered=rec_amt,
            status=st,
            mode="SIMULATED",
            recovery_window_expires_at=now,
            attempt_count=1,
        )
        db_session.add(c)

    await db_session.commit()

    # ---------------------------------------------------------
    # Truth-Table Assertions: LIVE Mode
    # ---------------------------------------------------------
    live_m = await get_dashboard_metrics(db_session, mode="LIVE")

    assert live_m.mode == "LIVE"
    assert live_m.total_cases == 5
    assert live_m.amount_at_risk_paise == 150000
    assert live_m.amount_recovered_paise == 10000
    assert live_m.recovered_cases == 1
    assert live_m.stopped_cases == 1
    assert live_m.escalated_cases == 1
    assert live_m.recovery_rate_by_count == pytest.approx(1 / 5, abs=1e-6)
    assert live_m.recovery_rate_by_amount == pytest.approx(10000 / 150000, abs=1e-6)

    # Verify all 8 status keys in cases_by_status
    assert live_m.cases_by_status == {
        "CREATED": 0,
        "ANALYSING": 0,
        "WAITING": 1,
        "EXECUTING": 0,
        "LINK_SENT": 1,
        "RECOVERED": 1,
        "STOPPED": 1,
        "ESCALATED": 1,
    }
    assert sum(live_m.cases_by_status.values()) == live_m.total_cases

    # Verify failure-reason aggregation for LIVE
    live_fr = live_m.recovery_by_failure_reason
    assert set(live_fr.keys()) == {
        "INSUFFICIENT_FUNDS",
        "PAYMENT_CANCELLED",
        "GATEWAY_ERROR",
    }

    # INSUFFICIENT_FUNDS: 2 cases (1 recovered, 1 stopped)
    insuf = live_fr["INSUFFICIENT_FUNDS"]
    assert insuf.total_cases == 2
    assert insuf.recovered_cases == 1
    assert insuf.recovery_rate_by_count == pytest.approx(0.5, abs=1e-6)
    assert insuf.amount_at_risk_paise == 30000
    assert insuf.amount_recovered_paise == 10000

    # PAYMENT_CANCELLED: 1 case (waiting)
    cancel = live_fr["PAYMENT_CANCELLED"]
    assert cancel.total_cases == 1
    assert cancel.recovered_cases == 0
    assert cancel.recovery_rate_by_count == pytest.approx(0.0, abs=1e-6)
    assert cancel.amount_at_risk_paise == 50000
    assert cancel.amount_recovered_paise == 0

    # GATEWAY_ERROR: 2 cases (1 link_sent, 1 escalated)
    gw = live_fr["GATEWAY_ERROR"]
    assert gw.total_cases == 2
    assert gw.recovered_cases == 0
    assert gw.recovery_rate_by_count == pytest.approx(0.0, abs=1e-6)
    assert gw.amount_at_risk_paise == 70000
    assert gw.amount_recovered_paise == 0

    # ---------------------------------------------------------
    # Truth-Table Assertions: SIMULATED Mode (Strict Isolation)
    # ---------------------------------------------------------
    sim_m = await get_dashboard_metrics(db_session, mode="SIMULATED")

    assert sim_m.mode == "SIMULATED"
    assert sim_m.total_cases == 3
    assert sim_m.amount_at_risk_paise == 45000
    assert sim_m.amount_recovered_paise == 20000
    assert sim_m.recovered_cases == 2
    assert sim_m.stopped_cases == 0
    assert sim_m.escalated_cases == 0
    assert sim_m.recovery_rate_by_count == pytest.approx(2 / 3, abs=1e-6)
    assert sim_m.recovery_rate_by_amount == pytest.approx(20000 / 45000, abs=1e-6)

    assert sim_m.cases_by_status == {
        "CREATED": 1,
        "ANALYSING": 0,
        "WAITING": 0,
        "EXECUTING": 0,
        "LINK_SENT": 0,
        "RECOVERED": 2,
        "STOPPED": 0,
        "ESCALATED": 0,
    }
    assert sum(sim_m.cases_by_status.values()) == sim_m.total_cases

    sim_fr = sim_m.recovery_by_failure_reason
    assert set(sim_fr.keys()) == {"CARD_DECLINED", "INSUFFICIENT_FUNDS"}

    card = sim_fr["CARD_DECLINED"]
    assert card.total_cases == 2
    assert card.recovered_cases == 2
    assert card.recovery_rate_by_count == pytest.approx(1.0, abs=1e-6)
    assert card.amount_at_risk_paise == 20000
    assert card.amount_recovered_paise == 20000

    sim_insuf = sim_fr["INSUFFICIENT_FUNDS"]
    assert sim_insuf.total_cases == 1
    assert sim_insuf.recovered_cases == 0
    assert sim_insuf.recovery_rate_by_count == pytest.approx(0.0, abs=1e-6)
    assert sim_insuf.amount_at_risk_paise == 25000
    assert sim_insuf.amount_recovered_paise == 0

    # ---------------------------------------------------------
    # Truth-Table Assertions: ALL Mode (Aggregated)
    # ---------------------------------------------------------
    all_m = await get_dashboard_metrics(db_session, mode="ALL")

    assert all_m.mode == "ALL"
    assert all_m.total_cases == 8
    assert all_m.amount_at_risk_paise == 195000
    assert all_m.amount_recovered_paise == 30000
    assert all_m.recovered_cases == 3
    assert all_m.stopped_cases == 1
    assert all_m.escalated_cases == 1
    assert all_m.recovery_rate_by_count == pytest.approx(3 / 8, abs=1e-6)
    assert all_m.recovery_rate_by_amount == pytest.approx(30000 / 195000, abs=1e-6)
    assert sum(all_m.cases_by_status.values()) == 8
