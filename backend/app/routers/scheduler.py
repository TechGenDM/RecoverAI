from fastapi import APIRouter, BackgroundTasks

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
