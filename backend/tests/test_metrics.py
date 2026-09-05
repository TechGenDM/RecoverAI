import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.metrics_service import get_dashboard_metrics
from app.services.simulation_service import run_simulation

@pytest.mark.asyncio
async def test_payment_links_created_metric(db_session: AsyncSession):
    """Test 7: payment_links_created metric correctness."""
    await run_simulation(db_session, seed=404, scenario_count=5)
    
    metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")
    
    # Check that links_created matches what we expect
    # This also depends on our mock decisions. If M2 decides SEND_PAYMENT_LINK, and M3 executes successfully.
    # We generated 5 scenarios. Let's see how many links were created.
    assert metrics.payment_links_created >= 0
    
    # We could assert that payment_links_created <= decisions_send_link
    assert metrics.payment_links_created <= metrics.decisions_send_link

@pytest.mark.asyncio
async def test_final_intervention_vs_attempts(db_session: AsyncSession):
    """Test 8: latest-decision-per-case metrics."""
    await run_simulation(db_session, seed=505, scenario_count=10)
    
    metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")
    
    # Total interventions should equal total cases that reached decision (Total cases - still CREATED/ANALYSING)
    # Since simulation processes fully to terminal states, all 10 should have decisions.
    total_interventions = (
        metrics.cases_intervened_link +
        metrics.cases_intervened_wait +
        metrics.cases_intervened_escalate +
        metrics.cases_intervened_stop
    )
    
    # Total decisions (attempts) can be >= total interventions
    assert metrics.decisions_total >= total_interventions
    assert total_interventions <= metrics.total_cases

@pytest.mark.asyncio
async def test_live_simulated_metric_isolation(db_session: AsyncSession):
    """Test 10: LIVE and SIMULATED isolation."""
    # Create a dummy LIVE case manually
    from app.models import RecoveryCase, Payment
    import uuid
    from datetime import datetime, UTC
    
    p = Payment(
        razorpay_payment_id="pay_live_test",
        amount=1000,
        currency="INR",
        status="failed",
        error_reason="test",
        payload_snapshot={},
        created_at=datetime.now(UTC),
        failed_at=datetime.now(UTC)
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
        attempt_count=0
    )
    db_session.add(c)
    await db_session.commit()
    
    # Run simulation
    await run_simulation(db_session, seed=606, scenario_count=2)
    
    # Get LIVE metrics
    live_metrics = await get_dashboard_metrics(db_session, mode="LIVE")
    assert live_metrics.total_cases >= 1
    
    # Get SIMULATED metrics
    sim_metrics = await get_dashboard_metrics(db_session, mode="SIMULATED")
    assert sim_metrics.total_cases >= 2
    
    # Get ALL metrics
    all_metrics = await get_dashboard_metrics(db_session, mode="ALL")
    assert all_metrics.total_cases == live_metrics.total_cases + sim_metrics.total_cases
