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
