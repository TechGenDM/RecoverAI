import pytest
from datetime import UTC, datetime, timedelta
from app.services.simulation_service import get_deterministic_hash, run_simulation, SIM_EPOCH
from app.models import Payment, RecoveryCase, RecoveryAction, AuditEvent
from sqlalchemy import select, delete
from app.models import Payment, RecoveryCase, RecoveryAction, AuditEvent, RecoveryDecision

@pytest.fixture(autouse=True)
async def clean_db_before_test(db_session):
    """Ensure clean slate for each simulation test."""
    await db_session.execute(delete(AuditEvent))
    await db_session.execute(delete(RecoveryAction))
    await db_session.execute(delete(RecoveryDecision))
    await db_session.execute(delete(RecoveryCase))
    await db_session.execute(delete(Payment))
    await db_session.commit()

@pytest.mark.asyncio
async def test_sha256_determinism():
    """Test 1: SHA-256 Determinism."""
    hash1 = get_deterministic_hash(42, 0)
    hash2 = get_deterministic_hash(42, 0)
    hash3 = get_deterministic_hash(42, 1)
    
    assert hash1 == hash2
    assert hash1 != hash3
    assert 0.0 <= hash1 <= 1.0

@pytest.mark.asyncio
async def test_simulation_clock_isolation(db_session):
    """Test 2: Deterministic clock independence from wall clock."""
    # Run a simulation
    result = await run_simulation(db_session, seed=123, scenario_count=1)
    
    case = (await db_session.execute(select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED"))).scalars().first()
    assert case is not None
    
    payment = (await db_session.execute(select(Payment).where(Payment.razorpay_payment_id == case.original_payment_id))).scalars().first()
    assert payment is not None
    
    # Verify time is anchored to SIM_EPOCH, not real time
    assert payment.failed_at < SIM_EPOCH
    assert payment.failed_at.year == 2023 or payment.failed_at.year == 2024
    
    if case.resolved_at:
        assert case.resolved_at == SIM_EPOCH

@pytest.mark.asyncio
async def test_simulation_idempotency_zero_duplicates(db_session):
    """Test 3: Same seed/count rerun = zero duplicates."""
    res1 = await run_simulation(db_session, seed=456, scenario_count=3)
    assert res1["new_cases_processed"] == 3
    assert res1["already_existed"] == 0
    
    # Rerun
    res2 = await run_simulation(db_session, seed=456, scenario_count=3)
    assert res2["new_cases_processed"] == 0
    assert res2["already_existed"] == 3
    
    # Verify counts in DB
    cases = (await db_session.execute(select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED"))).scalars().all()
    assert len(cases) == 3

@pytest.mark.asyncio
async def test_scenario_expansion_preserves_identity(db_session):
    """Test 5 & 8: Same seed different scenario_count preserves prior indices."""
    res1 = await run_simulation(db_session, seed=789, scenario_count=2)
    
    cases_before = (await db_session.execute(select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED").order_by(RecoveryCase.original_payment_id))).scalars().all()
    
    res2 = await run_simulation(db_session, seed=789, scenario_count=4)
    assert res2["new_cases_processed"] == 2
    assert res2["already_existed"] == 2
    
    cases_after = (await db_session.execute(select(RecoveryCase).where(RecoveryCase.mode == "SIMULATED").order_by(RecoveryCase.original_payment_id))).scalars().all()
    
    assert cases_before[0].original_payment_id == cases_after[0].original_payment_id
    assert cases_before[1].original_payment_id == cases_after[1].original_payment_id

@pytest.mark.asyncio
async def test_simulated_plink_ids_populated(db_session):
    """Test 6: Synthetic plink IDs populated."""
    await run_simulation(db_session, seed=999, scenario_count=5)
    
    actions = (await db_session.execute(select(RecoveryAction).where(RecoveryAction.action_type == "SEND_PAYMENT_LINK"))).scalars().all()
    for action in actions:
        assert action.status == "SUCCESS"
        assert action.razorpay_link_id.startswith("plink_sim_")

@pytest.mark.asyncio
async def test_recovered_payment_idempotency(db_session):
    """Test 11 & 7: Recovered payment idempotency."""
    await run_simulation(db_session, seed=101, scenario_count=10)
    
    rec_cases = (await db_session.execute(select(RecoveryCase).where(RecoveryCase.status == "RECOVERED"))).scalars().all()
    
    payments = (await db_session.execute(select(Payment).where(Payment.status == "captured"))).scalars().all()
    
    assert len(rec_cases) == len(payments)
    
    for p in payments:
        assert p.razorpay_payment_id.startswith("pay_sim_rec_101_")

@pytest.mark.asyncio
async def test_no_invented_case_states(db_session):
    """Test 9: No invented EXPIRED case state."""
    await run_simulation(db_session, seed=202, scenario_count=20)
    
    cases = (await db_session.execute(select(RecoveryCase))).scalars().all()
    valid_states = {"CREATED", "ANALYSING", "WAITING", "EXECUTING", "LINK_SENT", "RECOVERED", "STOPPED", "ESCALATED"}
    for case in cases:
        assert case.status in valid_states

@pytest.mark.asyncio
async def test_partial_scenario_recovery(db_session):
    """Test 4: Partial scenario recovery/resume."""
    # First, run a simulation that we intercept or modify
    res1 = await run_simulation(db_session, seed=303, scenario_count=1)
    
    case = (await db_session.execute(select(RecoveryCase))).scalars().first()
    
    # Mutate to ANALYSING (simulate a crash during M2)
    case.status = "ANALYSING"
    await db_session.commit()
    
    res2 = await run_simulation(db_session, seed=303, scenario_count=1)
    
    case = (await db_session.execute(select(RecoveryCase))).scalars().first()
    # Should have recovered and completed
    assert case.status in ["RECOVERED", "STOPPED"]
