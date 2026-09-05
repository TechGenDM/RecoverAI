from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Payment, RecoveryCase
from app.services.analysis_service import analyze_and_decide
from app.services.scheduler import run_scheduler_tick

router = APIRouter(prefix="/v1/scheduler", tags=["scheduler"])


@router.post("/tick", status_code=202)
async def trigger_scheduler_tick(background_tasks: BackgroundTasks):
    """
    Triggers a run of the scheduler to process CREATED/WAITING recovery cases.
    Runs asynchronously in the background.
    """
    background_tasks.add_task(run_scheduler_tick)
    return {"message": "Scheduler tick queued"}


@router.post("/cases/{case_id}/analyze", status_code=200)
async def trigger_case_analysis(
    case_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Run M2 analysis strictly for a single case without running M3 execution."""
    stmt = (
        select(RecoveryCase)
        .options(selectinload(RecoveryCase.payment).selectinload(Payment.customer))
        .where(RecoveryCase.id == case_id)
    )
    case = (await db.execute(stmt)).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    await analyze_and_decide(db, case)
    await db.commit()
    await db.refresh(case)

    return {
        "case_id": str(case.id),
        "status": case.status,
    }
