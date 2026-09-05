from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel

from app.database import get_db
from app.config import settings
from app.schemas.dashboard import DashboardMetrics
from app.services.metrics_service import get_dashboard_metrics
from app.services.simulation_service import run_simulation
from app.models import RecoveryCase, AuditEvent

router = APIRouter(prefix="/v1", tags=["Dashboard"])

class SimulationRequest(BaseModel):
    seed: int
    scenario_count: int

@router.get("/metrics/recovery", response_model=DashboardMetrics)
async def get_recovery_metrics(
    mode: Optional[str] = Query(None, description="Filter by mode: LIVE or SIMULATED. Defaults to ALL."),
    session: AsyncSession = Depends(get_db)
):
    if mode and mode not in ["LIVE", "SIMULATED"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be LIVE or SIMULATED.")
    return await get_dashboard_metrics(session, mode)

@router.post("/simulation/run")
async def execute_simulation(
    request: SimulationRequest,
    session: AsyncSession = Depends(get_db)
):
    if not getattr(settings, "ENABLE_SIMULATION_ENDPOINT", True):
        raise HTTPException(status_code=403, detail="Simulation endpoint is disabled.")
    
    if request.scenario_count > 100:
        raise HTTPException(status_code=400, detail="Maximum scenario_count is 100 to prevent abuse.")
        
    if request.scenario_count < 1:
        raise HTTPException(status_code=400, detail="Minimum scenario_count is 1.")
        
    result = await run_simulation(session, request.seed, request.scenario_count)
    return result

@router.get("/cases")
async def list_cases(
    mode: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_db)
):
    stmt = select(RecoveryCase).order_by(desc(RecoveryCase.created_at))
    if mode:
        stmt = stmt.where(RecoveryCase.mode == mode)
    if status:
        stmt = stmt.where(RecoveryCase.status == status)
        
    stmt = stmt.limit(limit).offset(offset)
    cases = (await session.execute(stmt)).scalars().all()
    
    return [
        {
            "id": str(c.id),
            "original_payment_id": c.original_payment_id,
            "status": c.status,
            "mode": c.mode,
            "amount_at_risk": c.amount_at_risk,
            "amount_recovered": c.amount_recovered,
            "stop_reason": c.stop_reason,
            "created_at": c.created_at.isoformat(),
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        } for c in cases
    ]

@router.get("/cases/{case_id}/timeline")
async def get_case_timeline(
    case_id: str,
    session: AsyncSession = Depends(get_db)
):
    stmt = select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.created_at.asc())
    events = (await session.execute(stmt)).scalars().all()
    
    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "actor": e.actor,
            "mode": e.mode,
            "payload": e.payload,
            "created_at": e.created_at.isoformat()
        } for e in events
    ]
