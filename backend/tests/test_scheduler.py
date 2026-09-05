import pytest
from sqlalchemy import select

from app.models import AuditEvent, RecoveryCase
from app.services.scheduler import claim_batch, run_scheduler_tick


@pytest.mark.asyncio
async def test_scheduler_claim_batch(db_session, setup_test_data):
    # Setup test data should have created at least one CREATED case
    cases = await claim_batch(db_session, batch_size=5)

    assert len(cases) > 0

    # Check that they are now ANALYSING
    for case_id in cases:
        case = (
            await db_session.execute(
                select(RecoveryCase).where(RecoveryCase.id == case_id)
            )
        ).scalar_one()
        assert case.status == "ANALYSING"

        # Check audit log
        audit = (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.case_id == case.id,
                    AuditEvent.event_type == "SCHEDULER_CLAIMED",
                )
            )
        ).scalar_one_or_none()
        assert audit is not None


@pytest.mark.asyncio
async def test_run_scheduler_tick(db_session, setup_test_data):
    # This will claim and process
    # Need to be careful because run_scheduler_tick opens its own session using AsyncSessionLocal
    # We will test the tick and then use the test session to verify the DB state

    # Reset case status to CREATED for the test
    from sqlalchemy import update

    await db_session.execute(update(RecoveryCase).values(status="CREATED"))
    await db_session.commit()

    processed_count = await run_scheduler_tick()

    assert processed_count > 0

    # Check that cases are now in their final state (ANALYSING, WAITING, ESCALATED, STOPPED)
    db_session.expire_all()
    cases = (await db_session.execute(select(RecoveryCase))).scalars().all()
    for case in cases:
        # If it was processed, it shouldn't be CREATED anymore
        assert case.status != "CREATED"


@pytest.mark.asyncio
async def test_trigger_case_analysis_endpoint(db_session, setup_test_data):
    from fastapi.testclient import TestClient
    from sqlalchemy import update

    from app.main import app

    client = TestClient(app)

    # Reset a case to CREATED
    case = (await db_session.execute(select(RecoveryCase).limit(1))).scalar_one()
    await db_session.execute(
        update(RecoveryCase).where(RecoveryCase.id == case.id).values(status="CREATED")
    )
    await db_session.commit()

    resp = client.post(f"/v1/scheduler/cases/{case.id}/analyze")
    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == str(case.id)
    assert data["status"] in ("ANALYSING", "WAITING", "ESCALATED", "STOPPED")


@pytest.mark.asyncio
async def test_trigger_case_execution_endpoint(db_session, setup_test_data):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    case = (await db_session.execute(select(RecoveryCase).limit(1))).scalar_one()

    resp = client.post(f"/v1/scheduler/cases/{case.id}/execute")
    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == str(case.id)
    assert "status" in data
    assert "processed" in data
